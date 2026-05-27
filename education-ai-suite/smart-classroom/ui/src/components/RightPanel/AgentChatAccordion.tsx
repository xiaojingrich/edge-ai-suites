import React, { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { useAppSelector } from '../../redux/hooks';
import { streamAgentChat } from '../../services/api';
import Accordion from '../common/Accordion';
import { useTranslation } from 'react-i18next';
import '../../assets/css/AgentChat.css';

interface ThinkingStep {
  thought: string;
  action: string;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  thinkingSteps?: ThinkingStep[];
}

const ThinkingBlock: React.FC<{ steps: ThinkingStep[]; isActive: boolean }> = ({ steps, isActive }) => {
  const [collapsed, setCollapsed] = useState(false);
  const { t } = useTranslation();

  useEffect(() => {
    if (!isActive && steps.length > 0) {
      setCollapsed(true);
    }
  }, [isActive, steps.length]);

  if (steps.length === 0) return null;

  return (
    <div className={`agent-thinking-block ${collapsed ? 'collapsed' : ''}`}>
      <button
        className="agent-thinking-toggle"
        onClick={() => setCollapsed(!collapsed)}
      >
        {isActive && <span className="agent-thinking-indicator"></span>}
        <span>{isActive
          ? steps[steps.length - 1]?.thought || 'Thinking...'
          : t('agent.thinkingDone', `${steps.length} steps completed`)
        }</span>
        <span className="agent-thinking-chevron">{collapsed ? '▶' : '▼'}</span>
      </button>
      {!collapsed && (
        <ul className="agent-thinking-steps">
          {steps.map((s, i) => (
            <li key={i}>
              <span className="agent-step-action">{s.action}</span>
              {s.thought && <span className="agent-step-thought">{s.thought}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

const AgentChatAccordion: React.FC = () => {
  const { t } = useTranslation();
  const sessionId = useAppSelector(s => s.ui.sessionId);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, thinkingSteps]);

  useEffect(() => {
    setMessages([]);
    setConversationId(null);
  }, [sessionId]);

  const sendMessage = useCallback(async (userMessage: string) => {
    if (!userMessage.trim() || !sessionId || isStreaming) return;

    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    setIsStreaming(true);
    setIsThinking(true);
    setThinkingSteps([]);

    const controller = new AbortController();
    abortRef.current = controller;
    const collectedSteps: ThinkingStep[] = [];

    try {
      for await (const event of streamAgentChat(sessionId, userMessage, conversationId, { signal: controller.signal })) {
        if (event.type === 'agent_thinking') {
          const step: ThinkingStep = { thought: event.thought || '', action: event.action || '' };
          collectedSteps.push(step);
          setThinkingSteps([...collectedSteps]);
          if (event.conversationId && !conversationId) {
            setConversationId(event.conversationId);
          }
        } else if (event.type === 'agent_token' && event.token) {
          if (isThinking) {
            setIsThinking(false);
          }
          setMessages(prev => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === 'assistant' && last.thinkingSteps) {
              updated[updated.length - 1] = { ...last, content: last.content + event.token };
            } else {
              updated.push({ role: 'assistant', content: event.token, thinkingSteps: [...collectedSteps] });
              setThinkingSteps([]);
            }
            return updated;
          });
          if (event.conversationId && !conversationId) {
            setConversationId(event.conversationId);
          }
        } else if (event.type === 'error') {
          setIsThinking(false);
          setMessages(prev => [
            ...prev,
            { role: 'assistant', content: `Error: ${event.message}`, thinkingSteps: [...collectedSteps] },
          ]);
          setThinkingSteps([]);
        } else if (event.type === 'done') {
          if (event.conversationId && !conversationId) {
            setConversationId(event.conversationId);
          }
        }
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setMessages(prev => [
          ...prev,
          { role: 'assistant', content: `Connection error: ${err.message}`, thinkingSteps: [...collectedSteps] },
        ]);
      }
    } finally {
      setIsStreaming(false);
      setIsThinking(false);
      setThinkingSteps([]);
      abortRef.current = null;
    }
  }, [sessionId, isStreaming, conversationId, isThinking]);

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
  };

  const suggestedQuestions = [
    t('agent.suggest1', '今天学生表现怎么样？'),
    t('agent.suggest2', '哪个时间段参与度最低？'),
    t('agent.suggest3', '帮我生成一份完整的课堂评估报告'),
    t('agent.suggest4', '根据今天课程内容出5道测验题'),
  ];

  return (
    <Accordion title={t('agent.title', 'Class Report Agent')}>
      <div className="agent-chat-container">
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
              {msg.role === 'assistant' && msg.thinkingSteps && msg.thinkingSteps.length > 0 && (
                <ThinkingBlock steps={msg.thinkingSteps} isActive={false} />
              )}
              <div className={`agent-chat-msg agent-chat-msg-${msg.role}`}>
                <div className="agent-chat-msg-content">
                  {msg.role === 'assistant' ? (
                    <ReactMarkdown>{msg.content || '...'}</ReactMarkdown>
                  ) : (
                    <p>{msg.content}</p>
                  )}
                </div>
              </div>
            </React.Fragment>
          ))}
          {isThinking && thinkingSteps.length > 0 && (
            <ThinkingBlock steps={thinkingSteps} isActive={true} />
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="agent-chat-input-area">
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
    </Accordion>
  );
};

export default AgentChatAccordion;
