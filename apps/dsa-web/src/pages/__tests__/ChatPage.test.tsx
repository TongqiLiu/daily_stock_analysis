import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode, useState } from 'react';
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { createParsedApiError } from '../../api/error';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import { historyApi } from '../../api/history';
import type { ActiveStockContext } from '../../api/agent';
import type { Message, ProgressStep } from '../../stores/agentChatStore';
import { UI_LANGUAGE_STORAGE_KEY } from '../../utils/uiLanguage';
import ChatPage from '../ChatPage';
import { extractStockCodeFromMessage, extractStockCodesFromMessage } from '../../utils/chatStockCode';

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const {
  mockGetSkills,
  mockGetStatus,
  mockDeleteChatSession,
  mockSendChat,
  mockGetSystemConfig,
  mockUpdateSystemConfig,
  mockGetWatchlist,
  mockAddToWatchlist,
  mockRemoveFromWatchlist,
  mockDownloadSession,
  mockFormatSessionAsMarkdown,
  mockStockIndex,
  mockClipboardWriteText,
  mockStockIndexState,
} = vi.hoisted(() => {
  const mockStockIndex = [
    { canonicalCode: '600519.SH', displayCode: '600519', nameZh: '贵州茅台', aliases: ['茅台'], market: 'CN', assetType: 'stock', active: true },
    { canonicalCode: '300750.SZ', displayCode: '300750', nameZh: '宁德时代', aliases: [], market: 'CN', assetType: 'stock', active: true },
    { canonicalCode: '000001.SZ', displayCode: '000001', nameZh: '平安银行', aliases: [], market: 'CN', assetType: 'stock', active: true },
    { canonicalCode: 'BABA', displayCode: 'BABA', nameZh: '阿里巴巴', aliases: [], market: 'US', assetType: 'stock', active: true },
    { canonicalCode: '09988.HK', displayCode: '09988', nameZh: '阿里巴巴', aliases: [], market: 'HK', assetType: 'stock', active: true },
    { canonicalCode: 'sh000001', displayCode: 'sh000001', nameZh: '上证指数', aliases: ['000001.SH'], market: 'CN', assetType: 'index', active: true },
    { canonicalCode: 'sh000016', displayCode: 'sh000016', nameZh: '上证50', aliases: ['000016.SH'], market: 'CN', assetType: 'index', active: true },
    { canonicalCode: 'sz399001', displayCode: 'sz399001', nameZh: '深证成指', aliases: ['399001.SZ'], market: 'CN', assetType: 'index', active: true },
    { canonicalCode: 'sh000300', displayCode: 'sh000300', nameZh: '沪深300', aliases: ['sz399300', '399300.SZ', '000300.SH', '000300.CSI'], market: 'CN', assetType: 'index', active: true },
    { canonicalCode: 'csi930955', displayCode: '930955.CSI', nameZh: '红利低波100', aliases: [], market: 'CN', assetType: 'index', active: true },
  ];
  return {
    mockGetSkills: vi.fn(),
    mockClipboardWriteText: vi.fn(),
    mockGetStatus: vi.fn(),
    mockDeleteChatSession: vi.fn(),
    mockSendChat: vi.fn(),
    mockGetSystemConfig: vi.fn(),
    mockUpdateSystemConfig: vi.fn(),
    mockGetWatchlist: vi.fn(),
    mockAddToWatchlist: vi.fn(),
    mockRemoveFromWatchlist: vi.fn(),
    mockDownloadSession: vi.fn(),
    mockFormatSessionAsMarkdown: vi.fn(),
    mockStockIndex,
    // Mutable registry-load state for the async window shared by every backend.
    mockStockIndexState: {
      index: mockStockIndex,
      loading: false,
      error: null as Error | null,
      fallback: false,
      loaded: true,
    },
  };
});

const mockLoadSessions = vi.fn();
const mockLoadInitialSession = vi.fn();
const mockSwitchSession = vi.fn();
const mockStartStream = vi.fn();
const mockStopStream = vi.fn();
const mockStopSessionStream = vi.fn();
const mockRefreshSession = vi.fn();
const mockClearCompletionBadge = vi.fn();
const mockStartNewChat = vi.fn();
const mockIsSessionRunning = vi.fn(() => false);

const mockStoreState = {
  messages: [] as Message[],
  selectedSkillIds: null as string[] | null,
  activeStockContext: null as ActiveStockContext | null,
  stateContract: 'unbound' as 'unbound' | 'authoritative' | 'legacy',
  loading: false,
  progressSteps: [] as ProgressStep[],
  sessionId: 'session-1',
  sessions: [
    {
      session_id: 'session-1',
      title: '请简要分析 600519',
      message_count: 2,
      created_at: '2026-03-15T09:00:00Z',
      last_active: '2026-03-15T09:05:00Z',
    },
  ],
  sessionsLoading: false,
  chatError: null as ReturnType<typeof createParsedApiError> | null,
  stopping: false,
  terminalStatus: null as 'cancelled' | 'timeout' | null,
  stopError: false,
  loadSessions: mockLoadSessions,
  loadInitialSession: mockLoadInitialSession,
  switchSession: mockSwitchSession,
  stopStream: mockStopStream,
  stopSessionStream: mockStopSessionStream,
  refreshSession: mockRefreshSession,
  startStream: mockStartStream,
  clearCompletionBadge: mockClearCompletionBadge,
  isSessionRunning: mockIsSessionRunning,
};

vi.mock('../../api/agent', () => ({
  agentApi: {
    getSkills: mockGetSkills,
    getStatus: mockGetStatus,
    deleteChatSession: mockDeleteChatSession,
    sendChat: mockSendChat,
  },
}));

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    getConfig: mockGetSystemConfig,
    update: mockUpdateSystemConfig,
    getWatchlist: mockGetWatchlist,
    addToWatchlist: mockAddToWatchlist,
    removeFromWatchlist: mockRemoveFromWatchlist,
  },
}));

vi.mock('../../utils/chatExport', () => ({
  downloadSession: mockDownloadSession,
  formatSessionAsMarkdown: mockFormatSessionAsMarkdown,
}));

vi.mock('../../api/history', () => ({
  historyApi: {
    getDetail: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../../hooks/useStockIndex', () => ({
  useStockIndex: () => mockStockIndexState,
}));

vi.mock('../../stores/agentChatStore', () => {
  type MockStore = typeof mockStoreState & {
    setSelectedSkillIds: (skillIds: string[]) => void;
  };
  const useAgentChatStore = (
    selector?: (state: MockStore) => unknown
  ) => {
    const [selectedSkillIds, setSelectedSkillIdsState] = useState(
      mockStoreState.selectedSkillIds,
    );
    const state: MockStore = {
      ...mockStoreState,
      selectedSkillIds,
      setSelectedSkillIds: (skillIds) => {
        setSelectedSkillIdsState(skillIds);
      },
    };
    return typeof selector === 'function' ? selector(state) : state;
  };

  useAgentChatStore.getState = () => ({
    ...mockStoreState,
    startNewChat: mockStartNewChat,
  });

  return { useAgentChatStore };
});

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(prefers-color-scheme: dark)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });

  Object.defineProperty(window, 'requestAnimationFrame', {
    writable: true,
    value: (callback: FrameRequestCallback) => window.setTimeout(() => callback(0), 0),
  });

  Object.defineProperty(window, 'cancelAnimationFrame', {
    writable: true,
    value: (handle: number) => window.clearTimeout(handle),
  });

  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    writable: true,
    value: vi.fn(),
  });

  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: {
      writeText: mockClipboardWriteText,
    },
  });
});

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(mockStockIndexState, { index: mockStockIndex, loading: false,
    error: null, fallback: false, loaded: true });
  window.localStorage.removeItem(UI_LANGUAGE_STORAGE_KEY);
  mockGetStatus.mockReset();
  mockStoreState.messages = [];
  mockStoreState.activeStockContext = null;
  mockStoreState.stateContract = 'unbound';
  mockStoreState.selectedSkillIds = null;
  mockStoreState.loading = false;
  mockStoreState.progressSteps = [];
  mockStoreState.chatError = null;
  mockStoreState.stopping = false;
  mockStoreState.terminalStatus = null;
  mockStoreState.stopError = false;
  mockStoreState.sessionsLoading = false;
  mockStoreState.sessionId = 'session-1';
  mockIsSessionRunning.mockImplementation(() => false);
  mockStoreState.sessions = [
    {
      session_id: 'session-1',
      title: '请简要分析 600519',
      message_count: 2,
      created_at: '2026-03-15T09:00:00Z',
      last_active: '2026-03-15T09:05:00Z',
    },
  ];
  mockGetSkills.mockResolvedValue({
    skills: [
      { id: 'bull_trend', name: '趋势分析', description: '测试技能' },
    ],
    default_skill_id: 'bull_trend',
  });
  mockGetStatus.mockResolvedValue({
    backend: 'litellm',
    available: true,
    experimental: false,
    errorCode: null,
    message: null,
  });
  mockStartStream.mockImplementation(async (_payload, meta) => {
    meta?.onAccepted?.({
      type: 'accepted',
      backend: 'litellm',
      request_id: 'request-test',
      session_id: 'session-1',
    });
  });
  mockDeleteChatSession.mockResolvedValue(undefined);
  mockSendChat.mockResolvedValue({ success: true });
  mockGetWatchlist.mockResolvedValue([]);
  mockGetSystemConfig.mockResolvedValue({
    configVersion: 'cfg-v1',
    maskToken: 'mask-token',
    items: [
      {
        key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
        value: 'false',
        rawValueExists: true,
        isMasked: false,
      },
    ],
  });
  mockUpdateSystemConfig.mockResolvedValue({
    success: true,
    configVersion: 'cfg-v2',
    appliedCount: 1,
    skippedMaskedCount: 0,
    reloadTriggered: true,
    updatedKeys: ['AGENT_CONTEXT_COMPRESSION_ENABLED'],
    warnings: [],
  });
  mockDownloadSession.mockImplementation(() => {});
  mockFormatSessionAsMarkdown.mockReturnValue('# exported session');
  mockClipboardWriteText.mockResolvedValue(undefined);
});

describe('ChatPage', () => {
  it('lets the user stop an active analysis from the existing Chat composer', async () => {
    mockStoreState.loading = true;

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: '停止分析' }));

    expect(mockStopStream).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument();
  });

  it('offers the same stop control for LiteLLM analysis', async () => {
    mockStoreState.loading = true;

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '停止分析' }));
    expect(mockStopStream).toHaveBeenCalledTimes(1);
  });

  it('labels the stop action in English when the UI language is English', async () => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    mockStoreState.loading = true;

    render(
      <UiLanguageProvider>
        <MemoryRouter initialEntries={['/chat']}>
          <ChatPage />
        </MemoryRouter>
      </UiLanguageProvider>,
    );

    expect(await screen.findByRole('button', { name: 'Stop analysis' })).toBeInTheDocument();
  });

  it('shows a disabled stopping state until Codex confirms cleanup', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    mockStoreState.loading = true;
    mockStoreState.stopping = true;

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const button = await screen.findByRole('button', { name: '正在停止…' });
    expect(button).toBeDisabled();
  });

  it('shows a plain-language terminal status after cancellation', async () => {
    mockStoreState.terminalStatus = 'cancelled';

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('status')).toHaveTextContent('本次分析已停止，后台任务也已结束。');
  });

  it('shows the current backend in the existing Chat header', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Codex Agent · 实验')).toBeInTheDocument();
    expect(screen.getByText('Codex 当前可用范围')).toBeInTheDocument();
    expect(screen.getByText(/实时行情、新闻、市场热点/)).toBeInTheDocument();
    expect(screen.getByText('使用已保存的分析上下文和回测汇总，向 Codex 询问个股。')).toBeInTheDocument();
    expect(screen.getByText(/Codex 将基于已保存的分析上下文和回测汇总回答/)).toBeInTheDocument();
    expect(screen.queryByText(/AI 将调用实时数据工具/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '切换问股方式' })).toBeInTheDocument();
    expect(mockGetStatus).toHaveBeenCalledTimes(1);
    expect(screen.getByPlaceholderText(/分析 600519/)).toBeEnabled();
  });

  it('finishes the compatibility check when React Strict Mode remounts effects', async () => {
    render(
      <StrictMode>
        <MemoryRouter initialEntries={['/chat']}>
          <ChatPage />
        </MemoryRouter>
      </StrictMode>,
    );

    expect(await screen.findByPlaceholderText(/分析 600519/)).toBeEnabled();
    expect(screen.queryByText('正在确认问股运行环境')).not.toBeInTheDocument();
  });

  it('keeps the draft editable but disables sending while the compatibility check is pending', async () => {
    const status = createDeferred<{
      backend: string;
      available: boolean;
      experimental: boolean;
      errorCode: null;
      message: null;
    }>();
    mockGetStatus.mockReturnValueOnce(status.promise);

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('正在确认问股运行环境')).toBeInTheDocument();
    const input = screen.getByPlaceholderText(/分析 600519/);
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: '分析比亚迪' } });
    expect(screen.getByRole('button', { name: '分析比亚迪趋势' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    expect(screen.getByText(/不会调用模型或读取股票数据/)).toBeInTheDocument();
    status.resolve({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });

    await waitFor(() => expect(screen.getByRole('button', { name: '发送' })).toBeEnabled());
    expect(input).toHaveValue('分析比亚迪');
    expect(screen.getByRole('button', { name: '分析比亚迪趋势' })).toBeEnabled();
    expect(mockGetStatus).toHaveBeenCalledTimes(1);
  });

  it('blocks sending only when backend status confirms unavailability and links to Agent settings', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: false,
      experimental: true,
      errorCode: 'command_not_found',
      message: 'Codex was not found',
    });
    const router = createMemoryRouter(
      [
        { path: '/chat', element: <ChatPage /> },
        { path: '/settings', element: <div>Agent settings destination</div> },
      ],
      { initialEntries: ['/chat'] },
    );
    render(<RouterProvider router={router} />);

    const input = await screen.findByPlaceholderText(/分析 600519/);
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: '分析 AAPL' } });
    expect(input).toHaveValue('分析 AAPL');
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '前往 Agent 设置' }));
    expect(await screen.findByText('Agent settings destination')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?category=agent');
  });

  it('keeps the draft editable while sending is disabled when backend status cannot be established', async () => {
    mockGetStatus.mockRejectedValueOnce(new Error('temporary status failure'));
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('暂时无法读取问股运行状态')).toBeInTheDocument();
    const input = screen.getByPlaceholderText(/分析 600519/);
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: '分析 NFLX' } });
    expect(input).toHaveValue('分析 NFLX');
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    expect(mockGetStatus).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '重新检查' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '发送' })).toBeEnabled());
    expect(input).toHaveValue('分析 NFLX');
    expect(mockGetStatus).toHaveBeenCalledTimes(2);
  });

  it('keeps the draft until the server accepts the turn', async () => {
    const stream = createDeferred<void>();
    let onAccepted: ((event: {
      type: 'accepted';
      backend: 'litellm' | 'codex_app_server';
      request_id: string;
      session_id: string;
    }) => void) | undefined;
    mockStartStream.mockImplementationOnce(async (_payload, meta) => {
      onAccepted = meta?.onAccepted;
      await stream.promise;
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/分析 600519/);
    fireEvent.change(input, { target: { value: '分析 AAPL' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));

    expect(input).toHaveValue('分析 AAPL');
    expect(onAccepted).toBeTypeOf('function');
    act(() => {
      onAccepted?.({
        type: 'accepted',
        backend: 'codex_app_server',
        request_id: 'request-accepted',
        session_id: 'session-1',
      });
    });
    expect(input).toHaveValue('');

    stream.resolve();
    await act(async () => {
      await stream.promise;
    });
  });

  it('returns a manually expanded composer to its compact default after a turn is accepted', async () => {
    let onAccepted: ((event: {
      type: 'accepted';
      backend: 'litellm' | 'codex_app_server';
      request_id: string;
      session_id: string;
    }) => void) | undefined;
    mockStartStream.mockImplementationOnce(async (_payload, meta) => {
      onAccepted = meta?.onAccepted;
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/例如/);
    const resizeHandle = screen.getByRole('button', { name: '调整输入框高度' });
    vi.spyOn(textarea, 'getBoundingClientRect').mockReturnValue({
      x: 0, y: 0, width: 600, height: 80, top: 0, right: 600, bottom: 80, left: 0,
      toJSON: () => ({}),
    });
    fireEvent.keyDown(resizeHandle, { key: 'ArrowUp' });
    expect(textarea).toHaveStyle({ height: '104px' });

    fireEvent.change(textarea, { target: { value: '分析 AAPL' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(onAccepted).toBeTypeOf('function'));
    act(() => {
      onAccepted?.({
        type: 'accepted', backend: 'litellm', request_id: 'request-accepted', session_id: 'session-1',
      });
    });

    await waitFor(() => expect(textarea).toHaveStyle({ height: '44px' }));
  });

  it('does not clear another conversation draft when a background turn is accepted', async () => {
    let acceptedCallback: (() => void) | undefined;
    mockStartStream.mockImplementationOnce(async (_payload, meta) => { acceptedCallback = meta?.onAccepted; });
    render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);
    const input = await screen.findByPlaceholderText(/分析 600519/);
    fireEvent.change(input, { target: { value: '分析 ORCL' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(acceptedCallback).toBeTypeOf('function'));
    mockStoreState.sessionId = 'other-conversation';
    fireEvent.change(input, { target: { value: '尚未提交的新问题' } });
    act(() => acceptedCallback?.());
    expect(input).toHaveValue('尚未提交的新问题');
  });

  it('restores the last submitted message when pressing ArrowUp in an empty composer', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/分析 600519/);
    fireEvent.change(input, { target: { value: '分析 META 的价格行为和期权风险' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(input).toHaveValue(''));
    fireEvent.keyDown(input, { key: 'ArrowUp' });

    expect(input).toHaveValue('分析 META 的价格行为和期权风险');
    expect(mockStartStream).toHaveBeenCalledTimes(1);
  });

  it('does not overwrite a non-empty draft when pressing ArrowUp', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/分析 600519/);
    fireEvent.change(input, { target: { value: '第一次发送' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(input).toHaveValue(''));

    fireEvent.change(input, { target: { value: '正在编辑的新问题' } });
    fireEvent.keyDown(input, { key: 'ArrowUp' });

    expect(input).toHaveValue('正在编辑的新问题');
  });

  it.each(['litellm', 'codex_app_server'] as const)(
    'delegates code and name resolution to the server under %s, without waiting for a client registry',
    async (backend) => {
      mockGetStatus.mockResolvedValueOnce({ backend, available: true, experimental: false, errorCode: null, message: null });
      mockStockIndexState.index = [];
      mockStockIndexState.loading = true;
      render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);
      const input = await screen.findByPlaceholderText(/分析 600519/);
      for (const message of ['分析上证指数', '分析 005930.KS', '换成 000001', '比较 sh000001 和 000001']) {
        fireEvent.change(input, { target: { value: message } });
        fireEvent.click(screen.getByRole('button', { name: '发送' }));
        await waitFor(() => expect(mockStartStream).toHaveBeenLastCalledWith(
          expect.objectContaining({ message, context: undefined }), expect.anything()));
      }
      expect(screen.getByLabelText('当前讨论对象')).toHaveTextContent('未确认');
    },
  );

  it.each([
    ['sh000001', 'index', '上证指数'],
    ['000001', 'stock', '平安银行'],
    ['005930.KS', 'stock', '三星电子'],
  ] as const)('shows the accepted server identity %s and uses its type for watchlist eligibility', async (code, type, name) => {
    mockStoreState.stateContract = 'authoritative';
    mockStoreState.activeStockContext = {
      stock_code: code, canonical_id: code, stock_name: name, asset_type: type,
    };
    mockStockIndexState.index = [];
    mockStockIndexState.loading = true;
    render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);
    expect(await screen.findByLabelText('当前讨论对象')).toHaveTextContent(name);
    if (type === 'index') expect(screen.queryByText('加入自选')).not.toBeInTheDocument();
    else expect(await screen.findByText('加入自选')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), { target: { value: '风险呢' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenLastCalledWith(
      expect.objectContaining({ message: '风险呢', context: undefined }), expect.anything()));
  });

  it('keeps input and explicit Skill draft on an acceptance conflict and provides manual refresh, not auto resend', async () => {
    mockStoreState.chatError = createParsedApiError({
      title: '会话已更新', message: '请刷新会话后重试', code: 'session_state_conflict', category: 'http_error',
    });
    mockStartStream.mockImplementationOnce(async () => undefined);
    render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);
    const input = await screen.findByPlaceholderText(/分析 600519/);
    fireEvent.change(input, { target: { value: '风险呢' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));
    expect(input).toHaveValue('风险呢');
    fireEvent.click(screen.getByRole('button', { name: /刷新会话/ }));
    expect(mockRefreshSession).toHaveBeenCalledTimes(1);
    expect(input).toHaveValue('风险呢');
    expect(mockStartStream).toHaveBeenCalledTimes(1);
  });

  it('renders authoritative object and manual conflict refresh in English', async () => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    mockStoreState.activeStockContext = { stock_code: '005930.KS', canonical_id: '005930.KS',
      stock_name: 'Samsung', asset_type: 'stock' };
    mockStoreState.chatError = createParsedApiError({ title: 'Session changed', message: 'Refresh',
      code: 'session_state_conflict', category: 'http_error' });
    render(<UiLanguageProvider><MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter></UiLanguageProvider>);
    expect(await screen.findByLabelText('Current discussion object')).toHaveTextContent('Discussing: Samsung · 005930.KS');
    expect(screen.getByRole('button', { name: 'Refresh session (keep your draft and retry)' })).toBeInTheDocument();
  });

  it('renders the new Codex status copy in English when the UI language is English', async () => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: false,
      experimental: true,
      errorCode: 'command_not_found',
      message: 'Codex was not found',
    });

    render(
      <UiLanguageProvider>
        <MemoryRouter initialEntries={['/chat']}>
          <ChatPage />
        </MemoryRouter>
      </UiLanguageProvider>,
    );

    expect(await screen.findByText('Codex Agent · Experimental')).toBeInTheDocument();
    expect(screen.getByText('This device does not currently meet the basic Codex ask-stock requirements. Open Agent settings to check installation and Single Agent mode.')).toBeInTheDocument();
    expect(screen.queryByText(/当前不可用|前往 Agent 设置检查/)).not.toBeInTheDocument();
  });

  it('renders status-read failure copy in English', async () => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    mockGetStatus.mockRejectedValueOnce(new Error('temporary status failure'));

    render(
      <UiLanguageProvider>
        <MemoryRouter initialEntries={['/chat']}>
          <ChatPage />
        </MemoryRouter>
      </UiLanguageProvider>,
    );

    expect(await screen.findByText('Ask-stock status is temporarily unavailable')).toBeInTheDocument();
    expect(screen.getByText('The ask-stock runtime cannot be confirmed, so sending is paused. You can check again manually; your question will be preserved.')).toBeInTheDocument();
    expect(screen.queryByText('暂时无法读取问股运行状态')).not.toBeInTheDocument();
  });

  it('renders a fixed workspace shell with independent session and message viewports', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByTestId('chat-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('chat-session-list-scroll')).toBeInTheDocument();
    expect(screen.getByTestId('chat-message-scroll')).toBeInTheDocument();
    expect(mockLoadInitialSession).toHaveBeenCalled();
    expect(mockClearCompletionBadge).toHaveBeenCalled();
  });

  it('loads and saves the global context compression setting from the chat input area', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const compressionToggle = await screen.findByRole('checkbox', { name: /上下文压缩/ });

    await waitFor(() => {
      expect(compressionToggle).not.toBeDisabled();
    });

    expect(compressionToggle).not.toBeChecked();

    fireEvent.click(compressionToggle);

    await waitFor(() => {
      expect(mockUpdateSystemConfig).toHaveBeenCalledWith({
        configVersion: 'cfg-v1',
        maskToken: 'mask-token',
        reloadNow: true,
        items: [
          {
            key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
            value: 'true',
          },
        ],
      });
    });

    expect(compressionToggle).toBeChecked();
    expect(screen.getByText('已启用')).toBeInTheDocument();
  });

  it('rolls back the context compression switch when saving fails', async () => {
    mockGetSystemConfig.mockResolvedValue({
      configVersion: 'cfg-v1',
      maskToken: 'mask-token',
      items: [
        {
          key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
          value: 'true',
          rawValueExists: true,
          isMasked: false,
        },
      ],
    });
    mockUpdateSystemConfig.mockRejectedValue(
      createParsedApiError({
        title: '保存失败',
        message: '配置服务不可用',
        category: 'unknown',
      }),
    );

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const compressionToggle = await screen.findByRole('checkbox', { name: /上下文压缩/ });

    await waitFor(() => {
      expect(compressionToggle).toBeChecked();
      expect(compressionToggle).not.toBeDisabled();
    });

    fireEvent.click(compressionToggle);

    await waitFor(() => {
      expect(mockUpdateSystemConfig).toHaveBeenCalledWith(expect.objectContaining({
        items: [
          {
            key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
            value: 'false',
          },
        ],
      }));
      expect(compressionToggle).toBeChecked();
    });
    expect(screen.getByText('配置服务不可用')).toBeInTheDocument();
  });

  it('does not switch when clicking the current session card', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const sessionCard = await screen.findByRole('button', {
      name: /切换到对话 请简要分析 600519/,
    });

    fireEvent.click(sessionCard);
    expect(mockSwitchSession).not.toHaveBeenCalled();
    expect(sessionCard).toHaveAttribute('aria-current', 'page');
  });

  it('renders a separate delete button for each session and opens confirmation without switching', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const deleteButton = await screen.findByRole('button', {
      name: /删除对话 请简要分析 600519/,
    });

    fireEvent.click(deleteButton);

    expect(mockSwitchSession).not.toHaveBeenCalled();
    expect(await screen.findByText('删除后，该对话将不可恢复，确认删除吗？')).toBeInTheDocument();
  });

  it('hides header actions when there are no messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('heading', { name: '问股' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '导出会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发送到已配置的通知机器人/邮箱' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '历史对话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '复制当前对话' })).toBeDisabled();
  });

  it('shows sent and completed timestamps at the top of chat messages', async () => {
    mockStoreState.messages = [
      {
        id: 'user-timestamp',
        role: 'user',
        content: '请分析 NVDA',
        createdAt: '2026-03-16T09:00:00Z',
      },
      {
        id: 'assistant-timestamp',
        role: 'assistant',
        content: '等待突破确认。',
        completedAt: '2026-03-16T09:01:00Z',
      },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByTestId('chat-message-time-user-timestamp'))
      .toHaveTextContent('发送于 2026');
    expect(screen.getByTestId('chat-message-time-assistant-timestamp'))
      .toHaveTextContent('完成于 2026');
  });

  it('highlights bold execution fields by their risk-management meaning', async () => {
    mockStoreState.messages = [
      {
        id: 'assistant-execution-plan',
        role: 'assistant',
        content: '**建议动作：等待回踩**\n\n**仓位：单笔风险 0.5%**\n\n**止损位：100**\n\n**止盈位：120**\n\n**🟡 1R**',
      },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByText('建议动作：等待回踩')).toHaveClass('chat-key-emphasis--positive');
    expect(screen.getByText('仓位：单笔风险 0.5%')).toHaveClass('chat-key-emphasis--position');
    expect(screen.getByText('止损位：100')).toHaveClass('chat-key-emphasis--risk');
    expect(screen.getByText('止盈位：120')).toHaveClass('chat-key-emphasis--caution');
    expect(screen.getByText('🟡 1R')).toHaveClass('chat-key-emphasis--caution');
  });

  it('keeps execution tables in a keyboard-accessible horizontal scroll region', async () => {
    mockStoreState.messages = [{
      id: 'assistant-swing-table', role: 'assistant',
      content: '| 关键项 | 本轮唯一执行口径 |\n|---|---|\n| **🔴 硬止损** | 100 |',
    }];
    render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);
    const region = await screen.findByRole('region', { name: '分析表格，可横向滚动' });
    expect(region).toHaveClass('chat-table-scroll');
    expect(region).toHaveAttribute('tabindex', '0');
    expect(region.querySelector('table')).toHaveTextContent('硬止损');
    expect(screen.getByText('🔴 硬止损')).toHaveClass('chat-key-emphasis--risk');
  });

  it('copies the current conversation from the input toolbar', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 600519' },
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];
    mockFormatSessionAsMarkdown.mockReturnValue('# copied conversation');

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: '复制当前对话' }));

    await waitFor(() => {
      expect(mockFormatSessionAsMarkdown).toHaveBeenCalledWith(mockStoreState.messages);
      expect(mockClipboardWriteText).toHaveBeenCalledWith('# copied conversation');
    });
    expect(screen.getByRole('button', { name: '当前对话已复制' })).toBeInTheDocument();
  });

  it('exports the current session from the header action', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 600519' },
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: '导出会话为 Markdown 文件' }));

    expect(mockDownloadSession).toHaveBeenCalledWith(mockStoreState.messages);
    expect(mockFormatSessionAsMarkdown).not.toHaveBeenCalled();
  });

  it('renders assistant skill labels with shared badge semantics', async () => {
    mockStoreState.messages = [
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const skillBadge = await screen.findByLabelText('技能 趋势分析');
    expect(skillBadge).toBeInTheDocument();
    expect(skillBadge).toHaveTextContent('趋势分析');
  });

  it('renders assistant multi-skill labels with shared badge semantics', async () => {
    mockStoreState.messages = [
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '趋势偏强',
        skills: ['bull_trend', 'ma_golden_cross'],
        skillNames: ['趋势分析', '均线金叉'],
      },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const skillBadge = await screen.findByLabelText('技能 趋势分析、均线金叉');
    expect(skillBadge).toBeInTheDocument();
    expect(skillBadge).toHaveTextContent('趋势分析、均线金叉');
  });

  it('renders failed stage_done progress as a non-success state', async () => {
    mockStoreState.loading = true;
    mockStoreState.progressSteps = [
      { type: 'stage_done', stage: 'risk', status: 'failed' },
    ];
    mockStoreState.messages = [
      {
        id: 'assistant-1',
        role: 'assistant',
        content: 'Partial answer',
        thinkingSteps: [
          { type: 'stage_done', stage: 'risk', status: 'failed' },
        ],
      },
    ];

    const { container } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findAllByText('risk failed')).toHaveLength(1);

    const thinkingToggle = container.querySelector('button[class*="mb-2"][class*="w-full"]') as HTMLButtonElement;
    fireEvent.click(thinkingToggle);

    const failedStage = screen.getAllByText('risk failed').find((node) =>
      node.closest('.chat-progress-item'),
    );
    expect(failedStage).toBeDefined();
    expect(failedStage?.closest('.chat-progress-item')).toHaveClass('chat-progress-item-danger');
    expect(failedStage?.closest('.chat-progress-item')).not.toHaveClass('chat-progress-item-success');
  });

  it('renders pipeline budget skip progress without timeout severity', async () => {
    mockStoreState.loading = true;
    mockStoreState.progressSteps = [
      { type: 'pipeline_budget_skipped', stage: 'decision' },
    ];
    mockStoreState.messages = [
      {
        id: 'assistant-1',
        role: 'assistant',
        content: 'Partial answer',
        thinkingSteps: [
          { type: 'pipeline_budget_skipped', stage: 'decision' },
        ],
      },
    ];

    const { container } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findAllByText('decision skipped: insufficient budget')).toHaveLength(1);
    expect(screen.queryByText('decision timed out')).not.toBeInTheDocument();

    const thinkingToggle = container.querySelector('button[class*="mb-2"][class*="w-full"]') as HTMLButtonElement;
    fireEvent.click(thinkingToggle);

    const budgetSkipped = screen.getAllByText('decision skipped: insufficient budget').find((node) =>
      node.closest('.chat-progress-item'),
    );
    expect(budgetSkipped).toBeDefined();
    expect(budgetSkipped?.closest('.chat-progress-item')).toHaveClass('chat-progress-item-muted');
    expect(budgetSkipped?.closest('.chat-progress-item')).not.toHaveClass('chat-progress-item-danger');
  });

  it('selects the default skill after loading skills', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('checkbox', { name: '趋势分析' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '通用分析' })).not.toBeChecked();
  });

  it('shows the option strategy analysis selector from the backend catalog', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '趋势' },
        { id: 'options_strategy_analysis', name: '期权策略分析', description: '真实期权链与风险结构' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('checkbox', { name: '期权策略分析' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '期权策略分析' })).not.toBeChecked();
  });

  it('keeps value investing in core and preserves advanced selections when collapsed', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'value_investing', name: '价值投资', description: '财报证据与安全边际', catalog_group: 'core' },
        { id: 'vcp_h1_h2_buy', name: 'VCP H1/H2', description: '进阶形态', catalog_group: 'advanced' },
        { id: 'chan_theory', name: '缠论', description: '进阶结构', catalog_group: 'advanced' },
      ],
      default_skill_id: 'value_investing',
    });
    render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);

    const value = await screen.findByRole('checkbox', { name: '价值投资' });
    expect(value).toBeChecked();
    expect(screen.queryByRole('checkbox', { name: 'VCP H1/H2' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '用缠论分析茅台' })).not.toBeInTheDocument();
    const disclosure = screen.getByRole('button', { name: '展开进阶技能 (2)' });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(disclosure);
    fireEvent.click(screen.getByRole('checkbox', { name: 'VCP H1/H2' }));
    fireEvent.click(screen.getByRole('button', { name: '收起进阶技能 (2)' }));
    expect(screen.getByRole('checkbox', { name: 'VCP H1/H2' })).toBeChecked();
    expect(screen.queryByRole('checkbox', { name: '缠论' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), { target: { value: '分析 NVDA' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledWith(
      expect.objectContaining({ skills: ['value_investing', 'vcp_h1_h2_buy'] }), expect.any(Object),
    ));
  });

  it('restores an advanced skill from session history without opening the catalog', async () => {
    mockStoreState.selectedSkillIds = ['chan_theory'];
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'value_investing', name: '价值投资', description: '财报证据', catalog_group: 'core' },
        { id: 'chan_theory', name: '缠论', description: '结构分析', catalog_group: 'advanced' },
      ],
      default_skill_id: 'value_investing',
    });
    render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);
    expect(await screen.findByRole('checkbox', { name: '缠论' })).toBeChecked();
    expect(screen.getByRole('button', { name: '展开进阶技能 (1)' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('value template requires auditable financial evidence instead of forbidding research', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [{ id: 'value_investing', name: '价值投资', description: '财报证据', catalog_group: 'core' }],
      default_skill_id: 'value_investing',
    });
    render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '套用提示词模板 价投分析' }));
    const prompt = (screen.getByPlaceholderText(/分析 600519/) as HTMLTextAreaElement).value;
    expect(prompt).toContain('来源、报告期和单位');
    expect(prompt).toContain('股票价格回测不能证明价投策略胜率');
    expect(prompt).not.toContain('不要额外搜索新闻或研报');
    expect(prompt).toContain('输入不足时不得给合理买入价');
  });

  it('prefers multi-strategy consensus as default when available', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'multi_strategy_consensus', name: '⚡ 多策略联合', description: '联合评分' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('checkbox', { name: '⚡ 多策略联合' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '趋势分析' })).not.toBeChecked();
  });

  it('keeps multi-strategy consensus and option analysis together', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '趋势' },
        { id: 'multi_strategy_consensus', name: '⚡ 多策略联合', description: '联合评分' },
        { id: 'options_strategy_analysis', name: '期权策略分析', description: '期权链' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const consensus = await screen.findByRole('checkbox', { name: '⚡ 多策略联合' });
    const options = screen.getByRole('checkbox', { name: '期权策略分析' });
    expect(consensus).toBeChecked();
    fireEvent.click(options);
    expect(consensus).toBeChecked();
    expect(options).toBeChecked();
  });

  it('keeps the restored session skills when the Skill catalog finishes loading', async () => {
    mockStoreState.selectedSkillIds = ['ma_golden_cross'];
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'ma_golden_cross', name: '均线金叉', description: '均线交叉' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('checkbox', { name: '均线金叉' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '趋势分析' })).not.toBeChecked();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续分析' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({ skills: ['ma_golden_cross'] }),
        expect.any(Object),
      );
    });
  });

  it('replaces the default consensus skill when specialist frameworks are selected', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'multi_strategy_consensus', name: '⚡ 多策略联合', description: '联合评分' },
        { id: 'serenity_research', name: 'Serenity投研', description: '买方投研' },
        { id: 'value_investing', name: '💎 价值投资', description: '长期价值' },
      ],
      default_skill_id: 'multi_strategy_consensus',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const consensus = await screen.findByRole('checkbox', { name: '⚡ 多策略联合' });
    const serenity = screen.getByRole('checkbox', { name: 'Serenity投研' });
    const value = screen.getByRole('checkbox', { name: '💎 价值投资' });
    expect(consensus).toBeChecked();

    fireEvent.click(serenity);
    expect(consensus).not.toBeChecked();
    expect(serenity).toBeChecked();

    fireEvent.click(value);
    expect(serenity).toBeChecked();
    expect(value).toBeChecked();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 NFLX' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '分析 NFLX',
          skills: ['serenity_research', 'value_investing'],
        }),
        expect.objectContaining({
          skillNames: ['Serenity投研', '💎 价值投资'],
        }),
      );
    });
  });

  it('omits skills for an untouched new session so the server resolves its default', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('checkbox', { name: '趋势分析' })).toBeChecked();
    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 AAPL' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mockStartStream).toHaveBeenCalled());
    expect(mockStartStream.mock.calls.at(-1)?.[0]).not.toHaveProperty('skills');
  });

  it('omits skills when continuing a legacy session without persisted Skill state', async () => {
    mockStoreState.messages = [
      { id: 'legacy-user', role: 'user', content: '分析 AAPL' },
      { id: 'legacy-assistant', role: 'assistant', content: '历史分析结果' },
    ];
    mockStoreState.selectedSkillIds = null;

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('checkbox', { name: '趋势分析' })).toBeChecked();
    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续分析' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mockStartStream).toHaveBeenCalled());
    expect(mockStartStream.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        message: '继续分析',
        session_id: 'session-1',
      }),
    );
    expect(mockStartStream.mock.calls.at(-1)?.[0]).not.toHaveProperty('skills');
  });

  it('makes the consensus meta-skill replace an existing specialist selection', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'serenity_research', name: 'Serenity投研', description: '买方投研' },
        { id: 'value_investing', name: '💎 价值投资', description: '长期价值' },
        { id: 'multi_strategy_consensus', name: '⚡ 多策略联合', description: '联合评分' },
      ],
      default_skill_id: 'serenity_research',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const consensus = await screen.findByRole('checkbox', { name: '⚡ 多策略联合' });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Serenity投研' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '💎 价值投资' }));
    fireEvent.click(consensus);

    expect(consensus).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Serenity投研' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: '💎 价值投资' })).not.toBeChecked();
  });

  it('fills the chat input from a prompt template without sending immediately', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'multi_strategy_consensus', name: '⚡ 多策略联合', description: '联合评分' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const templateButton = await screen.findByRole('button', {
      name: '套用提示词模板 通用股票分析',
    });

    fireEvent.click(templateButton);

    const input = screen.getByPlaceholderText(/分析 600519/) as HTMLTextAreaElement;
    expect(input.value).toContain('请用多策略联合框架快速分析 {股票代码} 的当前交易价值');
    expect(input.value).toContain('模型正文控制在 900 字以内');
    expect(input.value).toContain('13 项证据明细与五维综合得分由评分工具和后端输出');
    expect(input.value).toContain('只给计算方法与条件，不给固定仓位');
    expect(input.value).toContain('若判定为第 3 浪候选/启动，必须追加带日期价位的浪型图');
    expect(screen.getByRole('checkbox', { name: '⚡ 多策略联合' })).toBeChecked();
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  it('exposes the Chinese option-combination analysis template', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'options_strategy_analysis', name: '期权策略分析', description: '真实期权链与组合盈亏' },
      ],
      default_skill_id: 'options_strategy_analysis',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const templateButton = await screen.findByRole('button', {
      name: '套用提示词模板 期权组合分析',
    });
    fireEvent.click(templateButton);

    const input = screen.getByPlaceholderText(/分析 600519/) as HTMLTextAreaElement;
    expect(input.value).toContain('期权组合策略分析');
    expect(input.value).toContain('组合级期权策略分析');
    expect(screen.getByRole('checkbox', { name: '期权策略分析' })).toBeChecked();
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  it('sends multiple selected skills in order', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'ma_golden_cross', name: '均线金叉', description: '均线交叉' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: '均线金叉' }));
    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 600519' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '分析 600519',
          skills: ['bull_trend', 'ma_golden_cross'],
        }),
        expect.objectContaining({
          skillNames: ['趋势分析', '均线金叉'],
          skillName: '趋势分析、均线金叉',
        }),
      );
    });
  });

  it('sends shortcut hints without treating them as confirmed state', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    mockGetSkills.mockResolvedValue({
      skills: [{ id: 'chan_theory', name: '缠论', description: '结构分析' }],
      default_skill_id: 'chan_theory',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );
    fireEvent.click(await screen.findByRole('button', { name: '用缠论分析茅台' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          context: { stock_code: '600519', stock_name: '贵州茅台' },
        }),
        expect.any(Object),
      );
    });
  });

  it('collapses the mobile skill picker by default and keeps selected skills when sending', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'ma_golden_cross', name: '均线金叉', description: '均线交叉' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const mobileToggle = await screen.findByRole('button', { name: '展开策略选择' });
    const skillPanel = screen.getByTestId('chat-skill-picker-panel');
    expect(mobileToggle).toHaveAttribute('aria-expanded', 'false');
    expect(skillPanel).toHaveClass('hidden');

    fireEvent.click(mobileToggle);

    expect(screen.getByRole('button', { name: '收起策略选择' })).toHaveAttribute('aria-expanded', 'true');
    expect(skillPanel).not.toHaveClass('hidden');
    expect(skillPanel).toHaveClass('flex');

    fireEvent.click(screen.getByRole('checkbox', { name: '均线金叉' }));
    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 600519' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '分析 600519',
          skills: ['bull_trend', 'ma_golden_cross'],
        }),
        expect.objectContaining({
          skillName: '趋势分析、均线金叉',
        }),
      );
    });

    expect(screen.getByRole('button', { name: '展开策略选择' })).toHaveAttribute('aria-expanded', 'false');
    expect(skillPanel).toHaveClass('hidden');
  });

  it('sends an explicit empty skills list when all concrete skills are cleared', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: '趋势分析' }));
    expect(screen.getByRole('checkbox', { name: '通用分析' })).toBeChecked();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 AAPL' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalled();
    });
    const lastCall = mockStartStream.mock.calls[mockStartStream.mock.calls.length - 1];
    expect(lastCall[0]).toEqual(expect.objectContaining({
      message: '分析 AAPL',
      skills: [],
    }));
    expect(lastCall[1]).toEqual(expect.objectContaining({
      skillNames: ['通用'],
      skillName: '通用',
    }));
  });

  it('caps concrete skill selection at three and re-enables choices after unselecting', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'ma_golden_cross', name: '均线金叉', description: '均线交叉' },
        { id: 'chan_theory', name: '缠论', description: '结构分析' },
        { id: 'wave_theory', name: '波浪理论', description: '波浪分析' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: '均线金叉' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '缠论' }));

    const wave = screen.getByRole('checkbox', { name: '波浪理论' });
    expect(wave).toBeDisabled();

    fireEvent.click(screen.getByRole('checkbox', { name: '均线金叉' }));
    expect(wave).not.toBeDisabled();
  });

  it('quick questions override the current multi-skill selection', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'ma_golden_cross', name: '均线金叉', description: '均线交叉' },
        { id: 'chan_theory', name: '缠论', description: '结构分析' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: '均线金叉' }));
    fireEvent.click(screen.getByRole('button', { name: '用缠论分析茅台' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '用缠论分析茅台',
          skills: ['chan_theory'],
        }),
        expect.objectContaining({
          skillNames: ['缠论'],
          skillName: '缠论',
        }),
      );
    });
    expect(mockStartStream.mock.calls.at(-1)?.[0]?.context).toEqual({ stock_code: '600519', stock_name: '贵州茅台' });
  });

  it('keeps a quick question in the input until the server accepts it', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [{ id: 'chan_theory', name: '缠论', description: '结构分析' }],
      default_skill_id: 'chan_theory',
    });
    mockStartStream.mockResolvedValueOnce(undefined);

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const quickQuestion = await screen.findByRole('button', { name: '用缠论分析茅台' });
    await waitFor(() => expect(quickQuestion).toBeEnabled());
    fireEvent.click(quickQuestion);

    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));
    expect(screen.getByPlaceholderText(/分析 600519/)).toHaveValue('用缠论分析茅台');
  });

  it('submits the A-share SMIC quick question with an unambiguous stock context', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    mockGetSkills.mockResolvedValue({
      skills: [{ id: 'box_oscillation', name: '箱体震荡', description: '震荡区间' }],
      default_skill_id: 'box_oscillation',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    await screen.findByText('Codex Agent · 实验');
    fireEvent.click(await screen.findByRole('button', { name: '用箱体震荡分析 A 股中芯国际 688981' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '用箱体震荡分析 A 股中芯国际 688981',
          skills: ['box_oscillation'],
          context: {
            stock_code: '688981',
            stock_name: '中芯国际',
          },
        }),
        expect.objectContaining({
          skillNames: ['箱体震荡'],
          skillName: '箱体震荡',
        }),
      );
    });
  });

  it('does not send a client-selected identity for a name-only question', async () => {
    mockGetStatus.mockResolvedValueOnce({ backend: 'codex_app_server', available: true, experimental: true, errorCode: null, message: null });
    render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);
    const input = await screen.findByPlaceholderText(/分析 600519/);
    fireEvent.change(input, { target: { value: '分析茅台' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledWith(
      expect.objectContaining({ message: '分析茅台', context: undefined }), expect.anything()));
  });

  it('does not guess when one stock name maps to multiple markets', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.change(await screen.findByPlaceholderText(/分析 600519/), {
      target: { value: '分析阿里巴巴' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({ context: undefined }),
        expect.any(Object),
      );
    });
  });

  it('keeps assistant message actions directly activatable in the DOM', async () => {
    mockStoreState.messages = [
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const exportButton = await screen.findByRole('button', { name: '导出此条消息为 Markdown' });
    const actionGroup = exportButton.parentElement;

    expect(actionGroup).toHaveClass('chat-message-actions');
    expect(actionGroup?.className).not.toMatch(/pointer-events-none|opacity-0/);
  });

  it('sends exported markdown to notification channel and shows success feedback', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 600519' },
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: false,
      experimental: true,
      errorCode: 'command_not_found',
      message: 'Codex was not found',
    });
    mockFormatSessionAsMarkdown.mockReturnValue('# exported markdown');

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: '发送到已配置的通知机器人/邮箱' }));

    await waitFor(() => {
      expect(mockFormatSessionAsMarkdown).toHaveBeenCalledWith(mockStoreState.messages);
      expect(mockSendChat).toHaveBeenCalledWith('# exported markdown');
    });

    expect(await screen.findByText('已发送到通知渠道')).toBeInTheDocument();
  });

  it('shows parsed error feedback when notification delivery fails', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 AAPL' },
      { id: 'assistant-1', role: 'assistant', content: '短线震荡', skillName: '趋势分析' },
    ];
    mockSendChat.mockRejectedValue(
      createParsedApiError({
        title: '发送失败',
        message: '通知渠道不可用',
        category: 'unknown',
      }),
    );

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: '发送到已配置的通知机器人/邮箱' }));

    expect(await screen.findByText('通知渠道不可用')).toBeInTheDocument();
  });

  it('prevents duplicate notification sends while the request is in flight', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 TSLA' },
      { id: 'assistant-1', role: 'assistant', content: '波动较大', skillName: '趋势分析' },
    ];
    const deferred = createDeferred<{ success: boolean }>();
    mockSendChat.mockImplementation(() => deferred.promise);

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const sendButton = await screen.findByRole('button', { name: '发送到已配置的通知机器人/邮箱' });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(mockSendChat).toHaveBeenCalledTimes(1);
      expect(sendButton).toBeDisabled();
    });

    fireEvent.click(sendButton);
    expect(mockSendChat).toHaveBeenCalledTimes(1);

    deferred.resolve({ success: true });

    await waitFor(() => {
      expect(sendButton).not.toBeDisabled();
    });
  });

  it('allows sending with base follow-up context before report hydration completes', async () => {
    const deferred = createDeferred<Awaited<ReturnType<typeof historyApi.getDetail>>>();

    vi.mocked(historyApi.getDetail).mockImplementation(() => deferred.promise);

    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0&recordId=1']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    const sendButton = screen.getByRole('button', { name: /发送|处理中\.\.\./ });
    expect(sendButton).not.toBeDisabled();
    expect(screen.getByText('正在加载历史分析上下文；现在可直接发送追问。')).toBeInTheDocument();

    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '请深入分析 贵州茅台(600519)',
          context: {
            stock_code: '600519',
            stock_name: '贵州茅台',
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });

    deferred.resolve({
      meta: {
        id: 1,
        queryId: 'q-1',
        stockCode: '600519',
        stockName: '贵州茅台',
        reportType: 'detailed',
        createdAt: '2026-03-18T08:00:00Z',
        currentPrice: 1523.6,
        changePct: 1.8,
      },
      summary: {
        analysisSummary: '趋势延续',
        operationAdvice: '继续观察',
        trendPrediction: '高位震荡',
        sentimentScore: 78,
      },
      strategy: {
        stopLoss: '1450',
      },
    });

    await waitFor(() => {
      expect(screen.queryByText('正在加载历史分析上下文；现在可直接发送追问。')).not.toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续分析成交量' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '继续分析成交量',
          context: undefined,
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '如果不考虑 TTM 呢' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '如果不考虑 TTM 呢',
          context: undefined,
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('uses hydrated report context when it finishes before sending', async () => {
    vi.mocked(historyApi.getDetail).mockResolvedValue({
      meta: {
        id: 1,
        queryId: 'q-1',
        stockCode: '600519',
        stockName: '贵州茅台',
        reportType: 'detailed',
        createdAt: '2026-03-18T08:00:00Z',
        currentPrice: 1523.6,
        changePct: 1.8,
      },
      summary: {
        analysisSummary: '趋势延续',
        operationAdvice: '继续观察',
        trendPrediction: '高位震荡',
        sentimentScore: 78,
      },
      strategy: {
        stopLoss: '1450',
      },
    });

    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0&recordId=1']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.queryByText('正在加载历史分析上下文；现在可直接发送追问。')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '请深入分析 贵州茅台(600519)',
          context: expect.objectContaining({
            stock_code: '600519',
            stock_name: '贵州茅台',
            previous_price: 1523.6,
            previous_change_pct: 1.8,
            previous_strategy: expect.objectContaining({
              stopLoss: '1450',
            }),
          }),
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('falls back to base stock context when recordId is missing', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=AAPL']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 AAPL')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '请深入分析 AAPL',
          context: {
            stock_code: 'AAPL',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
    expect(historyApi.getDetail).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续看估值' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '继续看估值',
          context: undefined,
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('never replays history or a prior report hint as authority after the first acceptance', async () => {
    mockStoreState.messages = [
      { id: '1', role: 'user', content: '分析600519' },
      { id: '2', role: 'user', content: '改看宁德时代' },
      { id: '3', role: 'assistant', content: '模型猜测正在讨论300750' },
    ];
    mockStoreState.stateContract = 'authoritative';
    render(<MemoryRouter initialEntries={['/chat?stock=AAPL&name=Apple']}><ChatPage /></MemoryRouter>);
    const input = await screen.findByDisplayValue('请深入分析 Apple(AAPL)');
    expect(screen.getByLabelText('当前讨论对象')).toHaveTextContent('未确认');
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));
    expect(mockStartStream.mock.calls[0][0].context).toEqual({ stock_code: 'AAPL', stock_name: 'Apple' });
    fireEvent.change(input, { target: { value: '风险呢' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(2));
    expect(mockStartStream.mock.calls[1][0].context).toBeUndefined();
    expect(screen.getByLabelText('当前讨论对象')).toHaveTextContent('未确认');
  });

  it.each(['风险呢', '改看300750', '比较600519和AAPL', 'AAPL与600519哪个更好'])(
    'does not overwrite or retransmit server state when submitting %s', async (message) => {
      mockStoreState.activeStockContext = { stock_code: '600519', canonical_id: 'sh600519',
        stock_name: '贵州茅台', asset_type: 'stock' };
      mockStoreState.stateContract = 'authoritative';
      render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);
      const input = await screen.findByPlaceholderText(/分析 600519/);
      fireEvent.change(input, { target: { value: message } });
      fireEvent.click(screen.getByRole('button', { name: '发送' }));
      await waitFor(() => expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({ message, context: undefined }), expect.anything()));
      expect(screen.getByLabelText('当前讨论对象')).toHaveTextContent('贵州茅台');
    },
  );

  it('ignores malformed follow-up query params', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=%3Cscript%3E&name=Bad%0AName&recordId=abc']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('heading', { name: '问股' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/分析 600519/)).toHaveValue('');
    expect(historyApi.getDetail).not.toHaveBeenCalled();
  });

  it('reprocesses follow-up query params when navigating to the same chat route again', async () => {
    const firstDeferred = createDeferred<Awaited<ReturnType<typeof historyApi.getDetail>>>();
    const secondDeferred = createDeferred<Awaited<ReturnType<typeof historyApi.getDetail>>>();

    vi.mocked(historyApi.getDetail)
      .mockImplementationOnce(() => firstDeferred.promise)
      .mockImplementationOnce(() => secondDeferred.promise);

    const router = createMemoryRouter(
      [{ path: '/chat', element: <ChatPage /> }],
      {
        initialEntries: ['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0&recordId=1'],
      },
    );

    render(<RouterProvider router={router} />);

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();
    expect(screen.getByText('正在加载历史分析上下文；现在可直接发送追问。')).toBeInTheDocument();

    await router.navigate('/chat?stock=AAPL&name=Apple&recordId=2');

    expect(await screen.findByDisplayValue('请深入分析 Apple(AAPL)')).toBeInTheDocument();

    firstDeferred.resolve({
      meta: {
        id: 1,
        queryId: 'q-1',
        stockCode: '600519',
        stockName: '贵州茅台',
        reportType: 'detailed',
        createdAt: '2026-03-18T08:00:00Z',
        currentPrice: 1523.6,
        changePct: 1.8,
      },
      summary: {
        analysisSummary: '趋势延续',
        operationAdvice: '继续观察',
        trendPrediction: '高位震荡',
        sentimentScore: 78,
      },
      strategy: {
        stopLoss: '1450',
      },
    });

    secondDeferred.resolve({
      meta: {
        id: 2,
        queryId: 'q-2',
        stockCode: 'AAPL',
        stockName: 'Apple',
        reportType: 'detailed',
        createdAt: '2026-03-18T09:00:00Z',
        currentPrice: 211.5,
        changePct: 2.4,
      },
      summary: {
        analysisSummary: '趋势走强',
        operationAdvice: '继续持有',
        trendPrediction: '短线偏强',
        sentimentScore: 81,
      },
      strategy: {
        stopLoss: '205',
      },
    });

    await waitFor(() => {
      expect(screen.queryByText('正在加载历史分析上下文；现在可直接发送追问。')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '请深入分析 Apple(AAPL)',
          context: expect.objectContaining({
            stock_code: 'AAPL',
            stock_name: 'Apple',
            previous_price: 211.5,
            previous_change_pct: 2.4,
            previous_strategy: expect.objectContaining({
              stopLoss: '205',
            }),
          }),
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it.each([
    ['sh000016', 'sh000016'],
    ['csi930955', 'csi930955'],
    ['000016.SH', 'sh000016'],
  ] as const)('restores the %s follow-up URL to the registry canonical and hides the stock-only watchlist action', async (stockParam, expectedCanonical) => {
    render(
      <MemoryRouter initialEntries={[`/chat?stock=${stockParam}`]}>
        <ChatPage />
      </MemoryRouter>,
    );

    const expectedPrompt = `请深入分析 ${expectedCanonical}`;
    expect(await screen.findByDisplayValue(expectedPrompt)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: expectedPrompt,
          context: {
            stock_code: expectedCanonical,
            stock_name: null,
          },
        }),
        expect.any(Object),
      );
    });

    // The index canonical hides the stock-only watchlist action immediately.
    expect(screen.queryByText('加入自选')).not.toBeInTheDocument();
    expect(screen.queryByText('从自选删除')).not.toBeInTheDocument();
  });

  it('defers a default-backend index follow-up until the registry settles', async () => {
    mockStockIndexState.index = [];
    mockStockIndexState.loading = true;
    mockStockIndexState.loaded = false;

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat?stock=sh000016']}>
        <ChatPage />
      </MemoryRouter>,
    );
    expect(screen.queryByDisplayValue(/请深入分析/)).not.toBeInTheDocument();

    mockStockIndexState.index = mockStockIndex;
    mockStockIndexState.loading = false;
    mockStockIndexState.loaded = true;
    rerender(
      <MemoryRouter initialEntries={['/chat?stock=sh000016']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByDisplayValue('请深入分析 sh000016')).toBeInTheDocument();
  });

  type RegistrySettleRow = [
    string,
    { index: typeof mockStockIndexState.index; error: Error | null; fallback: boolean },
    string,
  ];

  it.each<RegistrySettleRow>([
    [
      'success settle with index data',
      { index: mockStockIndex, error: null, fallback: false },
      '请深入分析 sh000016',
    ],
    [
      'explicit load failure fail-open',
      { index: [], error: new Error('registry unavailable'), fallback: true },
      '请深入分析 SH000016',
    ],
    [
      'successful-empty registry fail-open',
      { index: [], error: null, fallback: false },
      '请深入分析 SH000016',
    ],
  ])(
    'releases the default-backend index follow-up after the shared registry settles: %s',
    async (_scenario, finalRegistry, expectedPrompt) => {
      mockStockIndexState.index = [];
      mockStockIndexState.loading = true;
      mockStockIndexState.error = null;
      mockStockIndexState.fallback = false;
      mockStockIndexState.loaded = false;

      const { rerender } = render(
        <MemoryRouter initialEntries={['/chat?stock=sh000016']}>
          <ChatPage />
        </MemoryRouter>,
      );

      await waitFor(() => {
        expect(screen.queryByDisplayValue(/请深入分析 (SH000016|sh000016)/)).not.toBeInTheDocument();
      });

      // Settle regardless of outcome: success consumes the registry canonical;
      // failure and an empty registry fail open to the stock path.
      mockStockIndexState.index = finalRegistry.index;
      mockStockIndexState.loading = false;
      mockStockIndexState.error = finalRegistry.error;
      mockStockIndexState.fallback = finalRegistry.fallback;
      mockStockIndexState.loaded = true;
      rerender(
        <MemoryRouter initialEntries={['/chat?stock=sh000016']}>
          <ChatPage />
        </MemoryRouter>,
      );

      expect(await screen.findByDisplayValue(expectedPrompt)).toBeInTheDocument();
    },
  );

  it.each(['authoritative', 'legacy'] as const)(
    'does not restore a guessed stock from %s history when the store explicitly has no object', async (contract) => {
      mockStoreState.stateContract = contract;
      mockStoreState.messages = [{ id: 'm-1', role: 'user', content: '分析 sh000016' }];
      render(<MemoryRouter initialEntries={['/chat']}><ChatPage /></MemoryRouter>);
      const input = await screen.findByPlaceholderText(/分析 600519/);
      expect(screen.getByLabelText('当前讨论对象')).toHaveTextContent('未确认');
      expect(screen.queryByText('加入自选')).not.toBeInTheDocument();
      fireEvent.change(input, { target: { value: '继续看支撑位' } });
      fireEvent.click(screen.getByRole('button', { name: '发送' }));
      await waitFor(() => expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: undefined }), expect.anything()));
    },
  );
  it('shows a jump-to-latest action when new content arrives while the user is away from bottom', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 600519' },
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const viewport = await screen.findByTestId('chat-message-scroll');
    Object.defineProperty(viewport, 'scrollTop', { configurable: true, value: 0 });
    Object.defineProperty(viewport, 'clientHeight', { configurable: true, value: 400 });
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: 1200 });

    fireEvent.scroll(viewport);

    mockStoreState.messages = [
      ...mockStoreState.messages,
      { id: 'assistant-2', role: 'assistant', content: '新的补充分析', skillName: '趋势分析' },
    ];

    rerender(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const jumpButton = await screen.findByRole('button', { name: '查看最新消息' });
    expect(jumpButton).toBeInTheDocument();

    fireEvent.click(jumpButton);

    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it('scrolls to the latest message after a historical session finishes loading', async () => {
    const switched = createDeferred<void>();
    mockSwitchSession.mockReturnValueOnce(switched.promise);
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '当前会话问题' },
      { id: 'assistant-1', role: 'assistant', content: '当前会话回答' },
    ];
    mockStoreState.sessions = [
      ...mockStoreState.sessions,
      {
        session_id: 'session-2',
        title: '很长的历史对话',
        message_count: 20,
        created_at: '2026-03-16T09:00:00Z',
        last_active: '2026-03-16T09:05:00Z',
      },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    await waitFor(() => expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled());
    vi.mocked(HTMLElement.prototype.scrollIntoView).mockClear();
    const viewport = screen.getByTestId('chat-message-scroll');
    Object.defineProperty(viewport, 'scrollTop', { configurable: true, writable: true, value: 0 });
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: 1200 });

    fireEvent.click(screen.getByRole('button', { name: '切换到对话 很长的历史对话' }));
    expect(mockSwitchSession).toHaveBeenCalledWith('session-2');
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();

    await act(async () => {
      switched.resolve();
      await switched.promise;
    });

    await waitFor(() => {
      expect(viewport.scrollTop).toBe(1200);
    });
  });

  it('lets users resize the input with the keyboard-accessible drag handle', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const textarea = await screen.findByPlaceholderText(/例如/);
    const resizeHandle = screen.getByRole('button', { name: '调整输入框高度' });
    vi.spyOn(textarea, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      width: 600,
      height: 80,
      top: 0,
      right: 600,
      bottom: 80,
      left: 0,
      toJSON: () => ({}),
    });

    fireEvent.keyDown(resizeHandle, { key: 'ArrowUp' });
    expect(textarea).toHaveStyle({ height: '104px' });

    fireEvent.keyDown(resizeHandle, { key: 'ArrowDown' });
    expect(textarea).toHaveStyle({ height: '80px' });
  });
});

describe('extractStockCodeFromMessage', () => {
  it('returns 6-digit A-share code', () => {
    expect(extractStockCodeFromMessage('分析 600519 趋势')).toBe('600519');
    expect(extractStockCodeFromMessage('002460')).toBe('002460');
  });

  it('returns HK prefixed code (normalized)', () => {
    expect(extractStockCodeFromMessage('分析 hk00700')).toBe('HK00700');
  });

  it('returns .HK suffix code (normalized to canonical)', () => {
    expect(extractStockCodeFromMessage('00700.HK')).toBe('HK00700');
    expect(extractStockCodeFromMessage('1810.HK')).toBe('HK01810');
  });

  it('returns code with .SH/.SZ suffix (normalized)', () => {
    expect(extractStockCodeFromMessage('看 600519.SH')).toBe('600519');
    expect(extractStockCodeFromMessage('000001.SZ')).toBe('000001');
  });

  it('returns US ticker like AAPL', () => {
    expect(extractStockCodeFromMessage('分析 AAPL 走势')).toBe('AAPL');
    expect(extractStockCodeFromMessage('TSLA')).toBe('TSLA');
    expect(extractStockCodeFromMessage('分析 BRK.B')).toBe('BRK.B');
  });

  it('does NOT return finance abbreviations as tickers', () => {
    expect(extractStockCodeFromMessage('如果不考虑 TTM 呢')).toBeNull();
    expect(extractStockCodeFromMessage('市盈率 TTM 怎么看')).toBeNull();
    expect(extractStockCodeFromMessage('PE 怎么看')).toBeNull();
    expect(extractStockCodeFromMessage('MACD 还没金叉吗')).toBeNull();
    expect(extractStockCodeFromMessage('RSI 怎么看')).toBeNull();
    expect(extractStockCodeFromMessage('WHAT IS PE')).toBeNull();
    expect(extractStockCodeFromMessage('PE IS HIGH')).toBeNull();
    expect(extractStockCodeFromMessage('WHAT IS TTM')).toBeNull();
  });

  it('does NOT return contextual moving-average MA as a ticker', () => {
    expect(extractStockCodeFromMessage('分析 MA 均线')).toBeNull();
    expect(extractStockCodeFromMessage('看看 MA 怎么排列')).toBeNull();
    expect(extractStockCodesFromMessage('MA 和 RSI 的指标怎么看')).toEqual([]);
    expect(extractStockCodeFromMessage('分析 KDJ 指标')).toBeNull();
    expect(extractStockCodeFromMessage('KDJ 怎么看')).toBeNull();
  });

  it('skips finance abbreviations before a real ticker', () => {
    expect(extractStockCodeFromMessage('PE AAPL 怎么看')).toBe('AAPL');
    expect(extractStockCodeFromMessage('TTM AAPL 怎么看')).toBe('AAPL');
    expect(extractStockCodeFromMessage('MACD AAPL 怎么看')).toBe('AAPL');
    expect(extractStockCodeFromMessage('WHAT IS PE AAPL')).toBe('AAPL');
  });

  it('does NOT return exchange prefixes as tickers', () => {
    expect(extractStockCodeFromMessage('分析 SH 走势')).toBeNull();
    expect(extractStockCodeFromMessage('看看 BJ')).toBeNull();
    expect(extractStockCodeFromMessage('HK')).toBeNull();
    expect(extractStockCodeFromMessage('买入 SZ')).toBeNull();
    expect(extractStockCodeFromMessage('US 市场')).toBeNull();
    expect(extractStockCodeFromMessage('SS')).toBeNull();
  });

  it('returns null for messages without stock codes', () => {
    expect(extractStockCodeFromMessage('茅台现在适合买入吗')).toBeNull();
    expect(extractStockCodeFromMessage('大盘走势如何')).toBeNull();
  });

  it('matches prefixed code like SH600519 (normalized)', () => {
    expect(extractStockCodeFromMessage('分析 SH600519')).toBe('600519');
  });

  it('returns SZ-prefixed code when standalone (normalized)', () => {
    expect(extractStockCodeFromMessage('SZ000001')).toBe('000001');
  });

  it('returns all stock codes in message order', () => {
    expect(extractStockCodesFromMessage('分析 600519 和 AAPL 的差异')).toEqual(['600519', 'AAPL']);
    expect(extractStockCodesFromMessage('分析 AAPL 和 600519 的差异')).toEqual(['AAPL', '600519']);
    expect(extractStockCodesFromMessage('AAPL 和 TSLA 哪个更值得买')).toEqual(['AAPL', 'TSLA']);
    expect(extractStockCodesFromMessage('比较 BRK.B 和 AAPL')).toEqual(['BRK.B', 'AAPL']);
  });

  it('extracts lowercase tickers only with explicit stock intent hints', () => {
    expect(extractStockCodesFromMessage('分析tsla')).toEqual(['TSLA']);
    expect(extractStockCodesFromMessage('看看 tsla')).toEqual(['TSLA']);
    expect(extractStockCodesFromMessage('aapl 和 tsla 哪个更值得买')).toEqual(['AAPL', 'TSLA']);
    expect(extractStockCodesFromMessage('hello tsla')).toEqual([]);
  });

  it('returns all HK and A-share variants without exchange affix tokens', () => {
    expect(extractStockCodesFromMessage('比较 01810 和 AAPL')).toEqual(['HK01810', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 1810.HK 和 AAPL')).toEqual(['HK01810', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 600519.SH 和 AAPL')).toEqual(['600519', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 000001.SZ 和 SS')).toEqual(['000001']);
    expect(extractStockCodesFromMessage('比较 SH600519 和 AAPL')).toEqual(['600519', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 SZ000001 和 AAPL')).toEqual(['000001', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 BJ920748 和 AAPL')).toEqual(['920748', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 HK01810 和 AAPL')).toEqual(['HK01810', 'AAPL']);
  });

  it('does not return denied abbreviations in multi-code extraction', () => {
    expect(extractStockCodesFromMessage('如果不考虑 TTM 和 PE')).toEqual([]);
    expect(extractStockCodesFromMessage('MACD AAPL 和 RSI')).toEqual(['AAPL']);
    expect(extractStockCodesFromMessage('KDJ AAPL 怎么看')).toEqual(['AAPL']);
  });
});

describe('watchlist button with code variants', () => {
  it('shows "从自选删除" when canonical code is in watchlist and user inputs variant', async () => {
    mockStoreState.activeStockContext = { stock_code: '600519', canonical_id: 'sh600519', stock_name: null, asset_type: 'stock' };
    mockGetWatchlist.mockResolvedValue(['600519', 'HK01810']);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/例如/);
    fireEvent.change(textarea, { target: { value: '分析 600519.SH' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(await screen.findByText('从自选删除')).toBeInTheDocument();
  });

  it('shows "从自选删除" for HK variant codes', async () => {
    mockStoreState.activeStockContext = { stock_code: 'HK01810', canonical_id: 'hk01810', stock_name: null, asset_type: 'stock' };
    mockGetWatchlist.mockResolvedValue(['HK01810']);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/例如/);
    fireEvent.change(textarea, { target: { value: '分析 1810.HK' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(await screen.findByText('从自选删除')).toBeInTheDocument();
  });

  it('matches raw HK watchlist entries before rendering the watchlist action', async () => {
    mockStoreState.activeStockContext = { stock_code: 'HK01810', canonical_id: 'hk01810', stock_name: null, asset_type: 'stock' };
    mockGetWatchlist.mockResolvedValue(['01810']);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/例如/);
    fireEvent.change(textarea, { target: { value: '分析 1810.HK' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(await screen.findByText('从自选删除')).toBeInTheDocument();
  });

  it('removes the matched raw HK watchlist entry instead of adding a duplicate variant', async () => {
    mockStoreState.activeStockContext = { stock_code: 'HK00700', canonical_id: 'hk00700', stock_name: null, asset_type: 'stock' };
    mockGetWatchlist.mockResolvedValue(['00700']);
    mockRemoveFromWatchlist.mockResolvedValue([]);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/例如/);
    fireEvent.change(textarea, { target: { value: '分析 00700.HK' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });
    fireEvent.click(await screen.findByText('从自选删除'));

    await waitFor(() => {
      expect(mockRemoveFromWatchlist).toHaveBeenCalledWith('00700');
    });
    expect(mockAddToWatchlist).not.toHaveBeenCalled();
  });
});
