import { create } from 'zustand';
import { agentApi, decodeChatState, isAbortError } from '../api/agent';
import type { ActiveStockContext, ChatSessionItem, ChatStateFields, ChatStreamRequest, DecodedChatState } from '../api/agent';
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
  /** Message creation time from the persisted conversation, or local send time while streaming. */
  createdAt?: string;
  /** Set only after a streamed assistant response has completed successfully. */
  completedAt?: string;
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

export interface StreamAcceptedEvent extends ChatStateFields {
  type: 'accepted';
  backend: 'litellm' | 'codex_app_server';
  request_id: string;
  session_id: string;
  session_state?: { selected_skill_ids: string[] | null };
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
  if (event.error_code === 'session_state_conflict') {
    return getParsedApiError({ code: event.error_code, message: event.message });
  }
  const parsed = getParsedApiError(
    getFirstMeaningfulStreamError(
      event.error,
      event.message,
      event.content,
      fallbackMessage,
    ),
  );
  return event.error_code ? { ...parsed, code: event.error_code } : parsed;
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
  selectedSkillIds: string[] | null;
  savedSkillIds: string[] | null;
  activeStockContext: ActiveStockContext | null;
  stateContract: 'unbound' | DecodedChatState['contract'];
  sessionGeneration: string | null;
  sessionStateVersion: number | null;
  stateSource: 'detail' | 'accepted' | null;
  sessionEpoch: number;
  instanceBindingEpoch: number;
  detailReadSequence: number;
  skillDraftRevision: number;
  skillDraftDirty: boolean;
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
  /** Persisted or locally selected Skills keyed by session_id. */
  selectedSkillIdsBySession: Record<string, string[] | null>;
  /** Finished-session error/terminal banners retained until the session is viewed again. */
  sessionExtras: Record<string, SessionViewExtras>;
  /** Server identity and unsent Skill drafts remain isolated per conversation. */
  discussionBySession: Record<string, SessionDiscussionState>;
}

type SessionDiscussionState = Pick<AgentChatState,
  'activeStockContext' | 'savedSkillIds' | 'selectedSkillIds' | 'stateContract'
  | 'sessionGeneration' | 'sessionStateVersion' | 'stateSource'
  | 'instanceBindingEpoch' | 'skillDraftRevision' | 'skillDraftDirty'>;

interface AgentChatActions {
  setSelectedSkillIds: (skillIds: string[]) => void;
  setCurrentRoute: (path: string) => void;
  clearCompletionBadge: () => void;
  loadSessions: () => Promise<void>;
  loadInitialSession: () => Promise<void>;
  switchSession: (targetSessionId: string) => Promise<void>;
  refreshSession: () => Promise<void>;
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

const emptySessionState = {
  activeStockContext: null, savedSkillIds: null, selectedSkillIds: null,
  stateContract: 'unbound' as const, sessionGeneration: null, sessionStateVersion: null,
  stateSource: null, skillDraftRevision: 0, skillDraftDirty: false,
};
const emptyStreamState = {
  loading: false, progressSteps: [], abortController: null, activeRequestId: null,
  serverCancellation: false, stopping: false, terminalStatus: null, stopError: false,
};

// null rejects the response. {} keeps the newer state but can still acknowledge
// an accepted message from this owned request. Versions only compare within an instance.
function mergeChatState(current: SessionDiscussionState, incoming: DecodedChatState,
  source: 'detail' | 'accepted', bindingEpoch: number, draftRevision: number): Partial<SessionDiscussionState> | null {
  const sameInstance = incoming.contract === 'authoritative'
    && incoming.sessionGeneration !== null && incoming.sessionGeneration === current.sessionGeneration;
  if (!sameInstance) {
    if (bindingEpoch !== current.instanceBindingEpoch) return null;
    if (source === 'accepted' && incoming.contract === 'authoritative' && current.sessionGeneration !== null) return null;
  }
  if (sameInstance && current.sessionStateVersion !== null && incoming.sessionStateVersion !== null) {
    if (incoming.sessionStateVersion < current.sessionStateVersion) return source === 'detail' ? null : {};
    if (source === 'accepted' && incoming.sessionStateVersion === current.sessionStateVersion
      && current.stateSource === 'detail') return {};
  }
  const bindingChanged = current.sessionGeneration !== incoming.sessionGeneration
    || (current.stateContract === 'authoritative' && incoming.contract === 'legacy');
  return {
    stateContract: incoming.contract,
    activeStockContext: incoming.activeStockContext,
    sessionGeneration: incoming.sessionGeneration,
    sessionStateVersion: incoming.sessionStateVersion,
    savedSkillIds: incoming.selectedSkillIds,
    ...(draftRevision === current.skillDraftRevision && (source === 'accepted' || !current.skillDraftDirty)
      ? { selectedSkillIds: incoming.selectedSkillIds, skillDraftDirty: false } : {}),
    stateSource: source,
    instanceBindingEpoch: current.instanceBindingEpoch + (bindingChanged ? 1 : 0),
  };
}

function discussionForSession(state: AgentChatState, sessionId: string): SessionDiscussionState {
  if (state.sessionId !== sessionId) {
    return state.discussionBySession[sessionId] ?? { ...emptySessionState, instanceBindingEpoch: 0 };
  }
  const { activeStockContext, savedSkillIds, selectedSkillIds, stateContract, sessionGeneration,
    sessionStateVersion, stateSource, instanceBindingEpoch, skillDraftRevision, skillDraftDirty } = state;
  return { activeStockContext, savedSkillIds, selectedSkillIds, stateContract, sessionGeneration,
    sessionStateVersion, stateSource, instanceBindingEpoch, skillDraftRevision, skillDraftDirty };
}

function storeDiscussionPatch(state: AgentChatState, sessionId: string,
  patch: Partial<SessionDiscussionState>): Partial<AgentChatState> {
  const next = { ...discussionForSession(state, sessionId), ...patch };
  return {
    discussionBySession: { ...state.discussionBySession, [sessionId]: next },
    selectedSkillIdsBySession: { ...state.selectedSkillIdsBySession, [sessionId]: next.selectedSkillIds },
    ...(state.sessionId === sessionId ? next : {}),
  };
}

export const useAgentChatStore = create<AgentChatState & AgentChatActions>((set, get) => {
  const resetSession = (sessionId: string) => {
    cacheCurrentSessionState();
    const previous = get();
    // Changing the visible conversation must not stop its background request.
    const discussion = previous.discussionBySession[sessionId] ?? { ...emptySessionState, instanceBindingEpoch: 0 };
    set({ ...discussion, sessionId,
      ...viewFieldsForSession(previous.streamsBySession, previous.messagesBySession, previous.sessionExtras, sessionId),
      sessionEpoch: previous.sessionEpoch + 1,
      detailReadSequence: previous.detailReadSequence + 1 });
    localStorage.setItem(STORAGE_KEY_SESSION, sessionId);
  };
  const readDetail = async () => {
    const owner = get();
    const sequence = owner.detailReadSequence + 1;
    set({ detailReadSequence: sequence });
    const ownsRead = () => get().sessionId === owner.sessionId
      && get().sessionEpoch === owner.sessionEpoch && get().detailReadSequence === sequence;
    try {
      const detail = await agentApi.getChatSessionMessages(owner.sessionId);
      if (!ownsRead() || detail.session_id !== owner.sessionId) return;
      const decoded = decodeChatState(detail, 'detail');
      const current = get();
      const patch = mergeChatState(current, decoded, 'detail', owner.instanceBindingEpoch, owner.skillDraftRevision);
      if (!patch) return;
      const replacedInstance = current.sessionGeneration !== null
        && current.sessionGeneration !== decoded.sessionGeneration;
      if (replacedInstance) current.abortController?.abort();
      const mapped = detail.messages.map((m) => ({ id: m.id, role: m.role, content: m.content,
        createdAt: m.created_at ?? undefined }));
      const cached = current.messagesBySession[owner.sessionId];
      const messages = !replacedInstance && cached
        && (current.streamsBySession[owner.sessionId] || cached.length > mapped.length) ? cached : mapped;
      set((s) => {
        const streams = { ...s.streamsBySession };
        if (replacedInstance) delete streams[owner.sessionId];
        return { ...storeDiscussionPatch(s, owner.sessionId, patch),
          ...(replacedInstance ? emptyStreamState : {}), streamsBySession: streams,
          messages, messagesBySession: { ...s.messagesBySession, [owner.sessionId]: messages }, chatError: null };
      });
    } catch (error) {
      if (ownsRead()) set({ chatError: getParsedApiError(error) });
    }
  };
  const deliverServerCancellation = async (requestId: string, sessionId: string): Promise<void> => {
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

  const cacheCurrentSessionState = () => {
    const {
      sessionId,
      messages,
      messagesBySession,
      selectedSkillIds,
      selectedSkillIdsBySession,
    } = get();
    set({
      ...storeDiscussionPatch(get(), sessionId, {}),
      messagesBySession: messages.length > 0 || messagesBySession[sessionId]
        ? {
            ...messagesBySession,
            [sessionId]: messages,
          }
        : messagesBySession,
      selectedSkillIdsBySession: {
        ...selectedSkillIdsBySession,
        [sessionId]: selectedSkillIds,
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
  ...emptySessionState,
  sessionEpoch: 0,
  instanceBindingEpoch: 0,
  detailReadSequence: 0,
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
  selectedSkillIdsBySession: {},
  sessionExtras: {},
  discussionBySession: {},

  setSelectedSkillIds: (skillIds) => set((s) => ({
    selectedSkillIds: skillIds,
    skillDraftRevision: s.skillDraftRevision + 1, skillDraftDirty: true,
    selectedSkillIdsBySession: {
      ...s.selectedSkillIdsBySession,
      [s.sessionId]: skillIds,
    },
  })),

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
    const { hasInitialLoad, sessionEpoch, instanceBindingEpoch, activeRequestId, sessionId } = get();
    if (hasInitialLoad) return;
    const savedId = localStorage.getItem(STORAGE_KEY_SESSION);
    set({ hasInitialLoad: true, sessionsLoading: true });

    try {
      const sessionList = await agentApi.getChatSessions();
      if (get().sessionEpoch !== sessionEpoch) return;
      set({ sessions: sessionList });

      // A list can update presentation without owning a destructive restore.
      // First acceptance changes the instance binding, not the route epoch;
      // a newly started creation request must also keep its live stream.
      const current = get();
      if (current.instanceBindingEpoch !== instanceBindingEpoch
        || current.sessionId !== sessionId || current.activeRequestId !== activeRequestId) return;
      if (savedId) {
        const sessionExists = sessionList.some((s) => s.session_id === savedId);
        if (sessionExists) {
          if (get().sessionId !== savedId) resetSession(savedId);
          await readDetail();
        } else {
          resetSession(generateUUID());
        }
      } else {
        localStorage.setItem(STORAGE_KEY_SESSION, get().sessionId);
      }
    } catch (error) {
      if (get().sessionEpoch === sessionEpoch) set({ chatError: getParsedApiError(error) });
    } finally {
      set({ sessionsLoading: false });
    }
  },

  switchSession: async (targetSessionId) => {
    const { sessionId, messages } = get();
    if (targetSessionId === sessionId && messages.length > 0) return;
    resetSession(targetSessionId);
    if (get().streamsBySession[targetSessionId]) return;
    await readDetail();
  },

  refreshSession: readDetail,

  startNewChat: () => {
    resetSession(generateUUID());
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
    const { sessionGeneration, instanceBindingEpoch, skillDraftRevision } = discussionForSession(get(), streamSessionId);

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
      createdAt: new Date().toISOString(),
      skills: payload.skills,
      skill: payload.skills?.[0],
      skillNames,
      skillName,
    };

    try {
      const response = await agentApi.chatStream(
        { ...payload, session_id: streamSessionId, request_id: requestId,
          ...(sessionGeneration ? { session_generation: sessionGeneration } : {}) },
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
        if (event.type !== 'accepted' && (
          (event.request_id !== undefined && event.request_id !== requestId)
          || (event.session_id !== undefined && event.session_id !== streamSessionId)
        )) return;
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
          const decoded = decodeChatState(event, 'accepted');
          const statePatch = mergeChatState(discussionForSession(get(), streamSessionId), decoded,
            'accepted', instanceBindingEpoch, skillDraftRevision);
          if (!statePatch) {
            // This request belonged to an instance superseded by a valid detail.
            ac.abort();
            return;
          }
          acceptedEvent = event as StreamAcceptedEvent;
          finalBackend = acceptedEvent.backend;
          // Both backends accept the server-side cooperative stop signal.  Keep
          // SSE open so the terminal cancellation event can confirm cleanup.
          patchOwnedStream({ serverCancellation: true });
          updateSessionMessages((prev) => [
            ...prev,
            { ...userMessage, backend: acceptedEvent!.backend },
          ]);
          set((s) => ({
            ...storeDiscussionPatch(s, streamSessionId, statePatch),
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
        if (event.type === 'error') {
          patchOwnedStream({ stopError: false });
          const failureEvent = event as unknown as StreamFailureEvent;
          throw getStreamFailureError(
            failureEvent,
            streamFailureFallback(failureEvent, '分析出错'),
          );
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
            completedAt: new Date().toISOString(),
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
