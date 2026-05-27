import React, { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { useAppSelector } from '../../redux/hooks';
import { streamAgentChat } from '../../services/api';
import Accordion from '../common/Accordion';
import { useTranslation } from 'react-i18next';
import '../../assets/css/AgentChat.css';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

const AgentChatAccordion: React.FC = () => {
  const { t } = useTranslation();
  const sessionId = useAppSelector(s => s.ui.sessionId);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    setMessages([]);
    setConversationId(null);
  }, [sessionId]);

  const sendMessage = useCallback(async (userMessage: string) => {
    if (!userMessage.trim() || !sessionId || isStreaming) return;

    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    setIsStreaming(true);
    setMessages(prev => [...prev, { role: 'assistant', content: '' }]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      for await (const event of streamAgentChat(sessionId, userMessage, conversationId, { signal: controller.signal })) {
        if (event.type === 'agent_token' && event.token) {
          setMessages(prev => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === 'assistant') {
              updated[updated.length - 1] = { ...last, content: last.content + event.token };
            }
            return updated;
          });
          if (event.conversationId && !conversationId) {
            setConversationId(event.conversationId);
          }
        } else if (event.type === 'error') {
          setMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: 'assistant', content: `Error: ${event.message}` };
            return updated;
          });
        } else if (event.type === 'done') {
          if (event.conversationId && !conversationId) {
            setConversationId(event.conversationId);
          }
        }
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setMessages(prev => {
          const updated = [...prev];
          updated[updated.length - 1] = { role: 'assistant', content: `Connection error: ${err.message}` };
          return updated;
        });
      }
    } finally {
      setIsStreaming(false);
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
          {messages.length === 0 && (
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
            <div key={i} className={`agent-chat-msg agent-chat-msg-${msg.role}`}>
              <div className="agent-chat-msg-content">
                {msg.role === 'assistant' ? (
                  <ReactMarkdown>{msg.content || '...'}</ReactMarkdown>
                ) : (
                  <p>{msg.content}</p>
                )}
              </div>
            </div>
          ))}
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
