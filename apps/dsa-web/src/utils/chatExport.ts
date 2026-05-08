import type { Message } from '../stores/agentChatStore';
import { looksLikeStockCode } from './validation';

/**
 * Format chat messages as Markdown for export.
 */
export function formatSessionAsMarkdown(messages: Message[]): string {
  const now = new Date();
  const timeStr = now.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });

  const lines: string[] = [
    '# 问股会话',
    '',
    `生成时间: ${timeStr}`,
    '',
  ];

  for (const msg of messages) {
    const heading = msg.role === 'user' ? '## 用户' : '## AI';
    if (msg.role === 'assistant' && msg.skillName) {
      lines.push(`${heading} (${msg.skillName})`);
    } else {
      lines.push(heading);
    }
    lines.push('');
    lines.push(msg.content);
    lines.push('');
  }

  return lines.join('\n');
}

// Filename sanitization: forbid path separators / Windows-reserved chars / ASCII control chars.
// eslint-disable-next-line no-control-regex
const FILENAME_FORBIDDEN_CHARS = /[\\/:*?"<>|\x00-\x1f]/g;

function sanitizeFilenameSegment(value: string, maxLength = 64): string {
  return value
    .replace(FILENAME_FORBIDDEN_CHARS, '')
    .replace(/\s+/g, '_')
    .slice(0, maxLength);
}

function pickStockCode(candidate: string): string | null {
  const normalized = candidate.trim().toUpperCase();
  if (!normalized) return null;
  return looksLikeStockCode(normalized) ? normalized : null;
}

/**
 * Common 2-letter words that look like a US ticker but are usually not.
 * Used to filter false positives such as "US.KEEL" → don't match leading "US".
 */
const COMMON_NON_TICKER_2_LETTERS = new Set([
  'US', 'HK', 'CN', 'JP', 'UK', 'EU', 'KR', 'TW', 'SG',
  'AI', 'IT', 'PM', 'AM', 'OK', 'NO', 'IN', 'ON', 'OF', 'TO', 'BY', 'AT', 'AS',
  'IS', 'BE', 'IF', 'SO', 'OR', 'MY', 'WE', 'HE', 'ME',
]);

function isLikelyTrueTicker(candidate: string): boolean {
  if (!looksLikeStockCode(candidate)) return false;
  // 2-letter pure-alpha tokens (e.g. "US", "HK") are too noisy in free text;
  // require ≥ 3 letters when not wrapped in parentheses.
  if (/^[A-Z]{1,2}$/.test(candidate) && COMMON_NON_TICKER_2_LETTERS.has(candidate)) {
    return false;
  }
  return true;
}

/**
 * Try to extract stock code from chat messages.
 *
 * Priority:
 *   1. Code wrapped in CJK or ASCII parentheses, e.g. 贵州茅台(600519) / Tesla(TSLA)
 *   2. Standalone token that matches stock code patterns (6-digit / HK\d+ / US ticker)
 *
 * Scans user messages from earliest to latest; returns the first match. Returns
 * null when no candidate is found (caller should fall back to legacy filename).
 */
export function extractStockCodeFromMessages(messages: Message[]): string | null {
  const userMessages = messages.filter((m) => m.role === 'user');
  if (userMessages.length === 0) return null;

  // Code inside CJK or ASCII parentheses: high confidence, accept any looksLike match.
  const parenRegex = /[（(]\s*([A-Za-z0-9.]+)\s*[)）]/g;
  // Free-text token candidates: 6-digit A-share, HK\d+, US ticker (≥3 letters here to avoid false positives).
  // Lookbehind/lookahead boundaries treat ASCII alnum as part of a word; CJK / punctuation count as boundary.
  const tokenRegex = /(?<![A-Za-z0-9])((?:HK\d{1,5}|hk\d{1,5}|\d{6}(?:\.[A-Z]{1,3})?|[A-Z]{3,5}(?:\.[A-Z]{1,2})?))(?![A-Za-z0-9])/g;

  for (const msg of userMessages) {
    const text = msg.content;

    parenRegex.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = parenRegex.exec(text)) !== null) {
      const code = pickStockCode(match[1] ?? '');
      if (code && isLikelyTrueTicker(code)) return code;
    }

    tokenRegex.lastIndex = 0;
    while ((match = tokenRegex.exec(text)) !== null) {
      const code = pickStockCode(match[1] ?? '');
      if (code && isLikelyTrueTicker(code)) return code;
    }
  }

  return null;
}

/**
 * Build a yyyymmdd date string from current local time.
 */
function buildDateStamp(): string {
  const now = new Date();
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
}

/**
 * Build session export filename. Prefers `<stockCode>_<yyyymmdd>.md` when a stock
 * code can be extracted from messages; otherwise falls back to the legacy form.
 */
export function buildSessionFilename(messages: Message[]): string {
  const dateStr = buildDateStamp();
  const stock = extractStockCodeFromMessages(messages);
  if (stock) {
    return `${sanitizeFilenameSegment(stock)}_${dateStr}.md`;
  }
  const now = new Date();
  const pad = (n: number) => n.toString().padStart(2, '0');
  const timeStr = pad(now.getHours()) + pad(now.getMinutes());
  return `问股会话_${dateStr}_${timeStr}.md`;
}

/**
 * Build single-message export filename. Prefers `<stockCode>_<yyyymmdd>_<role>.md`
 * when a stock code can be extracted from the surrounding messages; otherwise
 * falls back to `<role>-message-<id>.md`.
 */
export function buildMessageFilename(msg: Message, allMessages: Message[]): string {
  const role = msg.role === 'user' ? 'user' : 'assistant';
  const stock = extractStockCodeFromMessages(allMessages);
  if (stock) {
    return `${sanitizeFilenameSegment(stock)}_${buildDateStamp()}_${role}.md`;
  }
  return `${role}-message-${msg.id}.md`;
}

/**
 * Trigger browser download of session as .md file.
 * Revokes object URL after download to prevent memory leak.
 */
export function downloadSession(messages: Message[]): void {
  const content = formatSessionAsMarkdown(messages);
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const filename = buildSessionFilename(messages);

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
