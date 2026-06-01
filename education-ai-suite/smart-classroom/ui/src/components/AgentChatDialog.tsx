import React, { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { useAppSelector } from '../redux/hooks';
import { streamAgentChat, listConversations, getConversationMessages, deleteConversation, listSessions, BASE_URL } from '../services/api';
import type { PlanStep, ConversationPreview, SessionInfo } from '../services/api';
import { useTranslation } from 'react-i18next';
import '../assets/css/AgentChat.css';

type StepStatus = 'pending' | 'running' | 'done';

interface PlanState {
  steps: PlanStep[];
  statuses: StepStatus[];
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  plan?: PlanState;
  reportSessionId?: string;
}

const PlanBlock: React.FC<{ plan: PlanState; isActive: boolean }> = ({ plan, isActive }) => {
  const [collapsed, setCollapsed] = useState(false);
  const { t } = useTranslation();

  useEffect(() => {
    if (!isActive && plan.steps.length > 0) {
      setCollapsed(true);
    }
  }, [isActive, plan.steps.length]);

  if (plan.steps.length === 0) return null;

  const doneCount = plan.statuses.filter(s => s === 'done').length;
  const totalCount = plan.steps.length;

  return (
    <div className={`agent-plan-block ${collapsed ? 'collapsed' : ''}`}>
      <button
        className="agent-plan-toggle"
        onClick={() => setCollapsed(!collapsed)}
      >
        {isActive && <span className="agent-thinking-indicator"></span>}
        <span>{isActive
          ? `${doneCount}/${totalCount} ` + t('agent.planProgress', 'steps')
          : t('agent.planDone', { count: totalCount, defaultValue: '{{count}} steps completed' })
        }</span>
        <span className="agent-thinking-chevron">{collapsed ? '▶' : '▼'}</span>
      </button>
      {!collapsed && (
        <ul className="agent-plan-steps">
          {plan.steps.map((step, i) => (
            <li key={i} className={`agent-plan-step agent-plan-step-${plan.statuses[i]}`}>
              <span className="agent-plan-step-icon">
                {plan.statuses[i] === 'done' && '✓'}
                {plan.statuses[i] === 'running' && <span className="agent-thinking-indicator"></span>}
                {plan.statuses[i] === 'pending' && '○'}
              </span>
              <span className="agent-plan-step-action">{step.action}</span>
              {step.llm && <span className="agent-plan-step-llm">LLM</span>}
              <span className="agent-plan-step-thought">{step.thought}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

interface AgentChatDialogProps {
  open: boolean;
  onClose: () => void;
}

const AgentChatDialog: React.FC<AgentChatDialogProps> = ({ open, onClose }) => {
  const { t } = useTranslation();
  const globalSessionId = useAppSelector(s => s.ui.sessionId);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [activePlan, setActivePlan] = useState<PlanState | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [conversationList, setConversationList] = useState<ConversationPreview[]>([]);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const sessionId = selectedSessionId || globalSessionId;

  useEffect(() => {
    if (open) {
      listSessions().then(setSessions);
    }
  }, [open]);

  useEffect(() => {
    if (globalSessionId && !selectedSessionId) {
      setSelectedSessionId(globalSessionId);
    }
  }, [globalSessionId, selectedSessionId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, activePlan]);

  useEffect(() => {
    setMessages([]);
    setConversationId(null);
  }, [sessionId]);

  const handleSessionChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newSessionId = e.target.value;
    setSelectedSessionId(newSessionId);
    setMessages([]);
    setConversationId(null);
    setShowHistory(false);
  };

  const sendMessage = useCallback(async (userMessage: string) => {
    if (!userMessage.trim() || !sessionId || isStreaming) return;

    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    setIsStreaming(true);
    setActivePlan(null);

    const controller = new AbortController();
    abortRef.current = controller;
    let currentPlan: PlanState | null = null;

    try {
      for await (const event of streamAgentChat(sessionId, userMessage, conversationId, { signal: controller.signal })) {
        if (event.conversationId && !conversationId) {
          setConversationId(event.conversationId);
        }

        if (event.type === 'agent_plan') {
          const steps = event.steps || [];
          currentPlan = { steps, statuses: steps.map(() => 'pending' as StepStatus) };
          setActivePlan({ ...currentPlan });
        } else if (event.type === 'agent_plan_update') {
          const steps = event.steps || [];
          if (currentPlan) {
            const oldStatuses = currentPlan.statuses;
            const newStatuses = steps.map((_, i) => i < oldStatuses.length ? oldStatuses[i] : 'pending' as StepStatus);
            currentPlan = { steps, statuses: newStatuses };
          } else {
            currentPlan = { steps, statuses: steps.map(() => 'pending' as StepStatus) };
          }
          setActivePlan({ ...currentPlan });
        } else if (event.type === 'agent_step_start') {
          if (currentPlan && event.index !== undefined) {
            const idx = event.index < 0 ? currentPlan.statuses.length + event.index : event.index;
            if (idx >= 0 && idx < currentPlan.statuses.length) {
              currentPlan.statuses[idx] = 'running';
              setActivePlan({ ...currentPlan });
            }
          }
        } else if (event.type === 'agent_step_done') {
          if (currentPlan && event.index !== undefined) {
            const idx = event.index < 0 ? currentPlan.statuses.length + event.index : event.index;
            if (idx >= 0 && idx < currentPlan.statuses.length) {
              currentPlan.statuses[idx] = 'done';
              setActivePlan({ ...currentPlan });
            }
            setMessages(prev => {
              const last = prev[prev.length - 1];
              if (last?.role === 'assistant' && last.plan) {
                const updated = [...prev];
                updated[updated.length - 1] = { ...last, plan: { ...currentPlan! } };
                return updated;
              }
              return prev;
            });
          }
        } else if (event.type === 'agent_thinking') {
          // Legacy thinking events (from ReAct before plan is set) — ignored if plan already active
        } else if (event.type === 'agent_token' && event.token) {
          setMessages(prev => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === 'assistant') {
              updated[updated.length - 1] = { ...last, content: last.content + event.token };
            } else {
              updated.push({ role: 'assistant', content: event.token, plan: currentPlan || undefined });
              setActivePlan(null);
            }
            return updated;
          });
        } else if (event.type === 'report_ready') {
          setMessages(prev => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === 'assistant') {
              updated[updated.length - 1] = { ...last, reportSessionId: event.sessionId };
            }
            return updated;
          });
        } else if (event.type === 'error') {
          setMessages(prev => [
            ...prev,
            { role: 'assistant', content: `Error: ${event.message}`, plan: currentPlan || undefined },
          ]);
          setActivePlan(null);
        }
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setMessages(prev => [
          ...prev,
          { role: 'assistant', content: `Connection error: ${err.message}`, plan: currentPlan || undefined },
        ]);
      }
    } finally {
      setIsStreaming(false);
      setActivePlan(null);
      abortRef.current = null;
    }
  }, [sessionId, isStreaming, conversationId]);

  const handleSend = () => {
    sendMessage(input.trim());
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
  };

  const handleNewConversation = () => {
    setMessages([]);
    setConversationId(null);
    setShowHistory(false);
  };

  const handleToggleHistory = async () => {
    if (!showHistory && sessionId) {
      const list = await listConversations(sessionId);
      setConversationList(list);
    }
    setShowHistory(!showHistory);
  };

  const handleSelectConversation = async (convId: string) => {
    if (!sessionId) return;
    const msgs = await getConversationMessages(sessionId, convId);
    setMessages(msgs.map(m => ({ role: m.role as 'user' | 'assistant', content: m.content })));
    setConversationId(convId);
    setShowHistory(false);
  };

  const handleDeleteConversation = async (e: React.MouseEvent, convId: string) => {
    e.stopPropagation();
    if (!sessionId) return;
    await deleteConversation(sessionId, convId);
    setConversationList(prev => prev.filter(c => c.conversation_id !== convId));
    if (conversationId === convId) {
      setMessages([]);
      setConversationId(null);
    }
  };

  const suggestedQuestions = [
    t('agent.suggest1', '今天学生表现怎么样？'),
    t('agent.suggest2', '哪个时间段参与度最低？'),
    t('agent.suggest3', '帮我生成一份完整的课堂评估报告'),
    t('agent.suggest4', '根据今天课程内容出5道测验题'),
  ];

  if (!open) return null;

  return (
    <div className="agent-dialog-overlay" onClick={onClose}>
      <div className="agent-dialog" onClick={e => e.stopPropagation()}>
        <div className="agent-dialog-header">
          <span className="agent-dialog-title">{t('agent.title', 'Class Report Agent')}</span>
          <div className="agent-dialog-header-actions">
            <button
              className="agent-history-btn"
              onClick={handleToggleHistory}
              disabled={isStreaming}
              title={t('agent.history', 'Chat history')}
            >
              &#x2630;
            </button>
            {messages.length > 0 && (
              <button
                className="agent-new-conv-btn"
                onClick={handleNewConversation}
                disabled={isStreaming}
                title={t('agent.newConversation', 'New conversation')}
              >
                +
              </button>
            )}
            <button className="agent-dialog-close" onClick={onClose}>&times;</button>
          </div>
        </div>

        {sessions.length > 0 && (
          <div className="agent-session-selector">
            <select
              value={sessionId || ''}
              onChange={handleSessionChange}
              disabled={isStreaming}
              className="agent-session-select"
            >
              {sessions.map(s => (
                <option key={s.session_id} value={s.session_id}>
                  {s.session_id}
                  {s.has_report ? ' ✔' : ''}
                </option>
              ))}
            </select>
          </div>
        )}

        {showHistory && (
          <div className="agent-history-panel">
            <div className="agent-history-title">{t('agent.historyTitle', 'Recent Conversations')}</div>
            {conversationList.length === 0 ? (
              <p className="agent-history-empty">{t('agent.noHistory', 'No previous conversations')}</p>
            ) : (
              <ul className="agent-history-list">
                {conversationList.map(conv => (
                  <li
                    key={conv.conversation_id}
                    className={`agent-history-item ${conv.conversation_id === conversationId ? 'active' : ''}`}
                    onClick={() => handleSelectConversation(conv.conversation_id)}
                  >
                    <span className="agent-history-preview">{conv.preview || '...'}</span>
                    <span className="agent-history-meta">{conv.message_count} msgs</span>
                    <button
                      className="agent-history-delete"
                      onClick={(e) => handleDeleteConversation(e, conv.conversation_id)}
                      title={t('agent.deleteConversation', 'Delete')}
                    >
                      &times;
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="agent-chat-messages">
          {messages.length === 0 && !isStreaming && (
            <div className="agent-chat-welcome">
              <p>{t('agent.welcome', 'Ask me anything about this class session.')}</p>
              <div className="agent-suggestions">
                {suggestedQuestions.map((q, i) => (
                  <button
                    key={i}
                    className="agent-suggestion-btn"
                    onClick={() => sendMessage(q)}
                    disabled={!sessionId || isStreaming}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((msg, i) => (
            <React.Fragment key={i}>
              {msg.role === 'assistant' && msg.plan && msg.plan.steps.length > 0 && (
                <PlanBlock plan={msg.plan} isActive={false} />
              )}
              <div className={`agent-chat-msg agent-chat-msg-${msg.role}`}>
                <div className="agent-chat-msg-content">
                  {msg.role === 'assistant' ? (
                    <ReactMarkdown>{msg.content || '...'}</ReactMarkdown>
                  ) : (
                    <p>{msg.content}</p>
                  )}
                </div>
                {msg.reportSessionId && (
                  <a
                    className="agent-download-btn"
                    href={`${BASE_URL}/report/${msg.reportSessionId}/download`}
                    download
                  >
                    {t('agent.downloadReport', 'Download Word Report')}
                  </a>
                )}
              </div>
            </React.Fragment>
          ))}
          {activePlan && activePlan.steps.length > 0 && (
            <PlanBlock plan={activePlan} isActive={true} />
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="agent-chat-input-area">
          <textarea
            className="agent-chat-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={sessionId ? t('agent.inputPlaceholder', 'Ask about this class...') : t('agent.noSession', 'Start a session first')}
            disabled={!sessionId || isStreaming}
            rows={2}
          />
          {isStreaming ? (
            <button className="agent-chat-stop-btn" onClick={handleStop}>
              {t('agent.stop', 'Stop')}
            </button>
          ) : (
            <button
              className="agent-chat-send-btn"
              onClick={handleSend}
              disabled={!input.trim() || !sessionId}
            >
              {t('agent.send', 'Send')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default AgentChatDialog;
