import { create } from 'zustand';
import { agentApi, isAbortError } from '../api/agent';
import type { ChatSessionItem, ChatStreamRequest } from '../api/agent';
import {
  createParsedApiError,
  getParsedApiError,
  isApiRequestError,
  isParsedApiError,
  type ParsedApiError,
} from '../api/error';
import { generateUUID } from '../utils/uuid';

const STORAGE_KEY_SESSION = 'dsa_chat_session_id';

export interface ProgressStep {
  type: string;
  step?: number;
  stage?: string;
  tool?: string;
  display_name?: string;
  status?: string;
  success?: boolean;
  duration?: number;
  elapsed?: number;
  timeout?: number;
  remaining?: number;
  minimum?: number;
  reason?: string;
  message?: string;
  content?: string;
  meta?: Record<string, unknown>;
  backend?: string;
  error_code?: string;
  request_id?: string;
  session_id?: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  skills?: string[];
  skill?: string;
  skillNames?: string[];
  skillName?: string;
  thinkingSteps?: ProgressStep[];
  backend?: string;
}

export interface StreamMeta {
  skillNames?: string[];
  skillName?: string;
  onAccepted?: (event: StreamAcceptedEvent) => void;
}

export interface StreamAcceptedEvent {
  type: 'accepted';
  backend: 'litellm' | 'codex_app_server';
  request_id: string;
  session_id: string;
}

type StreamTerminalStatus = 'cancelled' | 'timeout' | null;

type StreamFailureEvent = {
  type: string;
  success?: boolean;
  content?: string;
  error?: unknown;
  message?: unknown;
  backend?: string;
  error_code?: string;
};

/** Per-session in-flight stream runtime. Keyed by session_id. */
export interface SessionStreamRuntime {
  abortController: AbortController;
  requestId: string;
  progressSteps: ProgressStep[];
  chatError: ParsedApiError | null;
  serverCancellation: boolean;
  stopping: boolean;
  terminalStatus: StreamTerminalStatus;
  stopError: boolean;
}

function streamFailureFallback(event: StreamFailureEvent, defaultMessage: string): string {
  return event.backend === 'codex_app_server'
    ? 'Codex Agent 暂时无法完成本次问股，请查看 Agent 设置中的运行状态。'
    : defaultMessage;
}

function getFirstMeaningfulStreamError(...candidates: Array<unknown>): unknown {
  for (const candidate of candidates) {
    if (typeof candidate === 'string') {
      if (candidate.trim() !== '') {
        return candidate;
      }
      continue;
    }

    if (candidate != null) {
      return candidate;
    }
  }

  return undefined;
}

function getStreamFailureError(
  event: StreamFailureEvent,
  fallbackMessage: string,
): ParsedApiError {
  return getParsedApiError(
    getFirstMeaningfulStreamError(
      event.error,
      event.message,
      event.content,
      fallbackMessage,
    ),
  );
}

function emptyViewStreamFields() {
  return {
    loading: false,
    progressSteps: [] as ProgressStep[],
    chatError: null as ParsedApiError | null,
    abortController: null as AbortController | null,
    activeRequestId: null as string | null,
    serverCancellation: false,
    stopping: false,
    terminalStatus: null as StreamTerminalStatus,
    stopError: false,
  };
}

type SessionViewExtras = {
  chatError: ParsedApiError | null;
  terminalStatus: StreamTerminalStatus;
};

function viewFieldsForSession(
  streamsBySession: Record<string, SessionStreamRuntime>,
  messagesBySession: Record<string, Message[]>,
  sessionExtras: Record<string, SessionViewExtras>,
  sessionId: string,
) {
  const stream = streamsBySession[sessionId];
  const messages = messagesBySession[sessionId] ?? [];
  const extras = sessionExtras[sessionId];
  if (!stream) {
    return {
      messages,
      ...emptyViewStreamFields(),
      chatError: extras?.chatError ?? null,
      terminalStatus: extras?.terminalStatus ?? null,
    };
  }
  return {
    messages,
    loading: true,
    progressSteps: stream.progressSteps,
    chatError: stream.chatError,
    abortController: stream.abortController,
    activeRequestId: stream.requestId,
    serverCancellation: stream.serverCancellation,
    stopping: stream.stopping,
    terminalStatus: stream.terminalStatus,
    stopError: stream.stopError,
  };
}

interface AgentChatState {
  messages: Message[];
  loading: boolean;
  progressSteps: ProgressStep[];
  sessionId: string;
  sessions: ChatSessionItem[];
  sessionsLoading: boolean;
  chatError: ParsedApiError | null;
  currentRoute: string;
  completionBadge: boolean;
  hasInitialLoad: boolean;
  abortController: AbortController | null;
  activeRequestId: string | null;
  serverCancellation: boolean;
  stopping: boolean;
  terminalStatus: StreamTerminalStatus;
  stopError: boolean;
  /** In-flight streams keyed by session_id (supports concurrent sessions). */
  streamsBySession: Record<string, SessionStreamRuntime>;
  /** Message cache keyed by session_id (keeps background streams' turns). */
  messagesBySession: Record<string, Message[]>;
  /** Finished-session error/terminal banners retained until the session is viewed again. */
  sessionExtras: Record<string, SessionViewExtras>;
}

interface AgentChatActions {
  setCurrentRoute: (path: string) => void;
  clearCompletionBadge: () => void;
  loadSessions: () => Promise<void>;
  loadInitialSession: () => Promise<void>;
  switchSession: (targetSessionId: string) => Promise<void>;
  startNewChat: () => void;
  stopStream: () => Promise<void>;
  stopSessionStream: (targetSessionId: string) => Promise<void>;
  startStream: (payload: ChatStreamRequest, meta?: StreamMeta) => Promise<void>;
  isSessionRunning: (targetSessionId: string) => boolean;
}

const getInitialSessionId = (): string =>
  typeof localStorage !== 'undefined'
    ? localStorage.getItem(STORAGE_KEY_SESSION) || generateUUID()
    : generateUUID();

export const useAgentChatStore = create<AgentChatState & AgentChatActions>((set, get) => {
  const deliverServerCancellation = async (
    requestId: string,
    sessionId: string,
  ): Promise<void> => {
    try {
      await agentApi.cancelChatStream(requestId);
    } catch {
      const stream = get().streamsBySession[sessionId];
      if (stream?.requestId === requestId) {
        set((s) => {
          const current = s.streamsBySession[sessionId];
          if (!current || current.requestId !== requestId) {
            return s;
          }
          const nextStreams = {
            ...s.streamsBySession,
            [sessionId]: { ...current, stopping: false, stopError: true },
          };
          return {
            streamsBySession: nextStreams,
            ...(s.sessionId === sessionId
              ? { stopping: false, stopError: true }
              : {}),
          };
        });
      }
    }
  };

  const cacheCurrentMessages = () => {
    const { sessionId, messages, messagesBySession } = get();
    if (messages.length === 0 && !messagesBySession[sessionId]) {
      return;
    }
    set({
      messagesBySession: {
        ...messagesBySession,
        [sessionId]: messages,
      },
    });
  };

  const stopSessionStreamInternal = async (targetSessionId: string): Promise<void> => {
    const stream = get().streamsBySession[targetSessionId];
    if (!stream || stream.stopping) return;

    if (!stream.serverCancellation) {
      stream.abortController.abort();
      return;
    }

    set((s) => {
      const current = s.streamsBySession[targetSessionId];
      if (!current) return s;
      const nextStreams = {
        ...s.streamsBySession,
        [targetSessionId]: { ...current, stopping: true, stopError: false },
      };
      return {
        streamsBySession: nextStreams,
        ...(s.sessionId === targetSessionId
          ? { stopping: true, stopError: false }
          : {}),
      };
    });
    await deliverServerCancellation(stream.requestId, targetSessionId);
  };

  return {
  messages: [],
  loading: false,
  progressSteps: [],
  sessionId: getInitialSessionId(),
  sessions: [],
  sessionsLoading: false,
  chatError: null,
  currentRoute: '',
  completionBadge: false,
  hasInitialLoad: false,
  abortController: null,
  activeRequestId: null,
  serverCancellation: false,
  stopping: false,
  terminalStatus: null,
  stopError: false,
  streamsBySession: {},
  messagesBySession: {},
  sessionExtras: {},

  setCurrentRoute: (path) => set({ currentRoute: path }),

  clearCompletionBadge: () => set({ completionBadge: false }),

  isSessionRunning: (targetSessionId) => Boolean(get().streamsBySession[targetSessionId]),

  loadSessions: async () => {
    set({ sessionsLoading: true });
    try {
      const sessions = await agentApi.getChatSessions();
      set({ sessions });
    } catch {
      // Ignore load errors
    } finally {
      set({ sessionsLoading: false });
    }
  },

  loadInitialSession: async () => {
    const { hasInitialLoad } = get();
    if (hasInitialLoad) return;
    set({ hasInitialLoad: true, sessionsLoading: true });

    try {
      const sessionList = await agentApi.getChatSessions();
      set({ sessions: sessionList });

      const savedId = localStorage.getItem(STORAGE_KEY_SESSION);
      if (savedId) {
        const sessionExists = sessionList.some((s) => s.session_id === savedId);
        if (sessionExists) {
          const msgs = await agentApi.getChatSessionMessages(savedId);
          if (msgs.length > 0) {
            const mapped = msgs.map((m) => ({
              id: m.id,
              role: m.role,
              content: m.content,
            }));
            set((s) => ({
              messages: mapped,
              messagesBySession: {
                ...s.messagesBySession,
                [savedId]: mapped,
              },
            }));
          }
        } else {
          const newId = generateUUID();
          set({ sessionId: newId });
          localStorage.setItem(STORAGE_KEY_SESSION, newId);
        }
      } else {
        localStorage.setItem(STORAGE_KEY_SESSION, get().sessionId);
      }
    } catch {
      // Ignore
    } finally {
      set({ sessionsLoading: false });
    }
  },

  switchSession: async (targetSessionId) => {
    const { sessionId, messages } = get();
    if (targetSessionId === sessionId && messages.length > 0) return;

    cacheCurrentMessages();

    const {
      streamsBySession,
      messagesBySession,
      sessionExtras,
    } = get();
    const cachedMessages = messagesBySession[targetSessionId];
    const hasActiveStream = Boolean(streamsBySession[targetSessionId]);

    // Do not abort the previous session's stream — it keeps running in the background.
    set({
      sessionId: targetSessionId,
      ...viewFieldsForSession(
        streamsBySession,
        messagesBySession,
        sessionExtras,
        targetSessionId,
      ),
    });
    localStorage.setItem(STORAGE_KEY_SESSION, targetSessionId);

    // Active stream already owns the live message cache; skip stale API overwrite.
    if (hasActiveStream) {
      return;
    }

    try {
      const msgs = await agentApi.getChatSessionMessages(targetSessionId);
      if (get().sessionId !== targetSessionId) {
        return;
      }
      // If a stream started while we were fetching, keep the live cache.
      if (get().streamsBySession[targetSessionId]) {
        return;
      }
      const mapped = msgs.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
      }));
      // Prefer fresher in-memory cache when it has more turns than the API snapshot
      // (e.g. a just-finished background stream that hasn't been re-fetched yet).
      const localCached = get().messagesBySession[targetSessionId];
      const useLocal = Boolean(
        localCached
        && localCached.length > mapped.length,
      );
      const nextMessages = useLocal ? localCached! : mapped;
      set((s) => ({
        messages: nextMessages,
        messagesBySession: {
          ...s.messagesBySession,
          [targetSessionId]: nextMessages,
        },
      }));
    } catch {
      // Keep whatever we already have in cache/view.
      if (cachedMessages && get().sessionId === targetSessionId) {
        set({ messages: cachedMessages });
      }
    }
  },

  startNewChat: () => {
    // Keep any in-flight streams running in the background.
    cacheCurrentMessages();
    const newId = generateUUID();
    set({
      sessionId: newId,
      messages: [],
      messagesBySession: {
        ...get().messagesBySession,
        [newId]: [],
      },
      ...emptyViewStreamFields(),
    });
    localStorage.setItem(STORAGE_KEY_SESSION, newId);
  },

  stopStream: async () => {
    await stopSessionStreamInternal(get().sessionId);
  },

  stopSessionStream: async (targetSessionId) => {
    await stopSessionStreamInternal(targetSessionId);
  },

  startStream: async (payload, meta) => {
    const storeSessionId = get().sessionId;
    const streamSessionId = payload.session_id || storeSessionId;

    // Only block if THIS session already has an in-flight stream.
    if (get().streamsBySession[streamSessionId]) return;

    const ac = new AbortController();
    const requestId = payload.request_id || generateUUID();
    const initialMessages = get().sessionId === streamSessionId
      ? get().messages
      : (get().messagesBySession[streamSessionId] ?? []);

    const runtime: SessionStreamRuntime = {
      abortController: ac,
      requestId,
      progressSteps: [],
      chatError: null,
      serverCancellation: false,
      stopping: false,
      terminalStatus: null,
      stopError: false,
    };

    set((s) => {
      const nextStreams = {
        ...s.streamsBySession,
        [streamSessionId]: runtime,
      };
      const nextMessagesBySession = {
        ...s.messagesBySession,
        [streamSessionId]: initialMessages,
      };
      const nextExtras = { ...s.sessionExtras };
      delete nextExtras[streamSessionId];
      return {
        streamsBySession: nextStreams,
        messagesBySession: nextMessagesBySession,
        sessionExtras: nextExtras,
        ...(s.sessionId === streamSessionId
          ? {
              loading: true,
              progressSteps: [],
              chatError: null,
              abortController: ac,
              activeRequestId: requestId,
              serverCancellation: false,
              stopping: false,
              terminalStatus: null,
              stopError: false,
            }
          : {}),
      };
    });

    const ownsStream = () => {
      const stream = get().streamsBySession[streamSessionId];
      return stream?.abortController === ac && stream.requestId === requestId;
    };
    const isViewingStreamSession = () => get().sessionId === streamSessionId;

    const patchOwnedStream = (
      patch: Partial<SessionStreamRuntime>,
      viewExtra?: Record<string, unknown>,
    ) => {
      set((s) => {
        const current = s.streamsBySession[streamSessionId];
        if (!current || current.abortController !== ac || current.requestId !== requestId) {
          return s;
        }
        const nextStreams = {
          ...s.streamsBySession,
          [streamSessionId]: { ...current, ...patch },
        };
        return {
          streamsBySession: nextStreams,
          ...(s.sessionId === streamSessionId
            ? {
                progressSteps: nextStreams[streamSessionId].progressSteps,
                chatError: nextStreams[streamSessionId].chatError,
                serverCancellation: nextStreams[streamSessionId].serverCancellation,
                stopping: nextStreams[streamSessionId].stopping,
                terminalStatus: nextStreams[streamSessionId].terminalStatus,
                stopError: nextStreams[streamSessionId].stopError,
                ...viewExtra,
              }
            : {}),
        };
      });
    };

    const updateSessionMessages = (updater: (prev: Message[]) => Message[]) => {
      set((s) => {
        const prev = s.messagesBySession[streamSessionId] ?? [];
        const nextMessages = updater(prev);
        const nextMessagesBySession = {
          ...s.messagesBySession,
          [streamSessionId]: nextMessages,
        };
        return {
          messagesBySession: nextMessagesBySession,
          ...(s.sessionId === streamSessionId ? { messages: nextMessages } : {}),
        };
      });
    };

    const skillNames = meta?.skillNames?.length
      ? meta.skillNames
      : [meta?.skillName ?? '通用'];
    const skillName = skillNames.join('、');

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: payload.message,
      skills: payload.skills,
      skill: payload.skills?.[0],
      skillNames,
      skillName,
    };

    try {
      const response = await agentApi.chatStream(
        { ...payload, session_id: streamSessionId, request_id: requestId },
        { signal: ac.signal },
      );
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let finalContent: string | null = null;
      let finalBackend: string | undefined;
      let receivedDoneEvent = false;
      let acceptedEvent: StreamAcceptedEvent | null = null;
      const currentProgressSteps: ProgressStep[] = [];
      const protocolError = (message: string) => createParsedApiError({
        title: '请求未被接受',
        message: 'Agent 没有确认接收本次问题，请保留当前内容后重试。',
        rawMessage: message,
        category: 'upstream_network',
      });
      const processLine = (line: string) => {
        if (!line.startsWith('data: ') || !ownsStream() || ac.signal.aborted) return;

        const event = JSON.parse(line.slice(6)) as ProgressStep;
        if (event.type === 'accepted') {
          if (acceptedEvent) {
            throw protocolError('Agent stream emitted accepted more than once.');
          }
          if (
            (event.backend !== 'litellm' && event.backend !== 'codex_app_server')
            || event.request_id !== requestId
            || event.session_id !== streamSessionId
          ) {
            throw protocolError('Agent stream emitted an invalid accepted event.');
          }
          acceptedEvent = event as StreamAcceptedEvent;
          finalBackend = acceptedEvent.backend;
          patchOwnedStream({
            serverCancellation: acceptedEvent.backend === 'codex_app_server',
          });
          updateSessionMessages((prev) => [
            ...prev,
            { ...userMessage, backend: acceptedEvent!.backend },
          ]);
          set((s) => ({
            sessions: s.sessions.some((x) => x.session_id === streamSessionId)
              ? s.sessions
              : [
                  {
                    session_id: streamSessionId,
                    title: payload.message.slice(0, 60),
                    message_count: 1,
                    created_at: new Date().toISOString(),
                    last_active: new Date().toISOString(),
                  },
                  ...s.sessions,
                ],
          }));
          meta?.onAccepted?.(acceptedEvent);
          return;
        }
        if (!acceptedEvent) {
          throw protocolError(`Agent stream emitted ${event.type || 'an unknown event'} before accepted.`);
        }
        if (event.type === 'done') {
          patchOwnedStream({ stopError: false });
          receivedDoneEvent = true;
          const doneEvent = event as unknown as StreamFailureEvent;
          if (doneEvent.error_code === 'cancelled') {
            patchOwnedStream({ terminalStatus: 'cancelled' });
            return;
          }
          if (doneEvent.error_code === 'timeout') {
            patchOwnedStream({ terminalStatus: 'timeout' });
            return;
          }
          if (doneEvent.success === false) {
            throw getStreamFailureError(
              doneEvent,
              streamFailureFallback(doneEvent, '大模型调用出错，请检查 API Key 配置'),
            );
          }
          finalContent = doneEvent.content ?? '';
          return;
        }

        if (event.type === 'error') {
          patchOwnedStream({ stopError: false });
          const failureEvent = event as unknown as StreamFailureEvent;
          throw getStreamFailureError(
            failureEvent,
            streamFailureFallback(failureEvent, '分析出错'),
          );
        }

        currentProgressSteps.push(event);
        patchOwnedStream({ progressSteps: [...currentProgressSteps] });
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';

        for (const line of lines) {
          try {
            processLine(line);
          } catch (parseErr: unknown) {
            if (isParsedApiError(parseErr) || isApiRequestError(parseErr)) {
              throw parseErr;
            }
          }
        }
      }

      if (buf.trim().startsWith('data: ')) {
        try {
          processLine(buf.trim());
        } catch (parseErr: unknown) {
          if (isParsedApiError(parseErr) || isApiRequestError(parseErr)) {
            throw parseErr;
          }
        }
      }

      if (!acceptedEvent && !ac.signal.aborted) {
        throw protocolError('Agent stream ended before accepted.');
      }

      if (!receivedDoneEvent && !ac.signal.aborted) {
        throw createParsedApiError({
          title: '回复未完整返回',
          message: 'Agent 流式响应在完成前中断，请重试。',
          rawMessage: 'Agent stream ended before a done event was received.',
          category: 'upstream_network',
        });
      }

      const { currentRoute } = get();
      const shouldAppend = ownsStream() && !ac.signal.aborted && finalContent !== null;

      if (shouldAppend) {
        updateSessionMessages((prev) => [
          ...prev,
          {
            id: (Date.now() + 1).toString(),
            role: 'assistant',
            content: finalContent || '（无内容）',
            skills: payload.skills,
            skill: payload.skills?.[0],
            skillNames,
            skillName,
            thinkingSteps: [...currentProgressSteps],
            backend: finalBackend,
          },
        ]);
      }

      if (ownsStream() && !ac.signal.aborted && (currentRoute !== '/chat' || !isViewingStreamSession())) {
        set({ completionBadge: true });
      }
    } catch (error: unknown) {
      if (isAbortError(error) || !ownsStream() || ac.signal.aborted) {
        // Aborted requests must not affect other sessions' chat state.
      } else {
        const parsed = getParsedApiError(error);
        patchOwnedStream({ chatError: parsed });
        const { currentRoute } = get();
        if (currentRoute !== '/chat' || !isViewingStreamSession()) {
          set({ completionBadge: true });
        }
      }
    } finally {
      if (ownsStream()) {
        const finishedTerminal = get().streamsBySession[streamSessionId]?.terminalStatus ?? null;
        const finishedError = get().streamsBySession[streamSessionId]?.chatError ?? null;
        set((s) => {
          const nextStreams = { ...s.streamsBySession };
          delete nextStreams[streamSessionId];
          const nextExtras = { ...s.sessionExtras };
          if (finishedTerminal || finishedError) {
            nextExtras[streamSessionId] = {
              chatError: finishedError,
              terminalStatus: finishedTerminal,
            };
          } else {
            delete nextExtras[streamSessionId];
          }
          return {
            streamsBySession: nextStreams,
            sessionExtras: nextExtras,
            ...(s.sessionId === streamSessionId
              ? {
                  loading: false,
                  progressSteps: [],
                  abortController: null,
                  activeRequestId: null,
                  serverCancellation: false,
                  stopping: false,
                  terminalStatus: finishedTerminal,
                  chatError: finishedError,
                  stopError: false,
                }
              : {}),
          };
        });
        await get().loadSessions();
      }
    }
  },
  };
});
