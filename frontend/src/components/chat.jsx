import React, { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const getCsrfToken = () => {
  return document
    .cookie
    .split("; ")
    .find((row) => row.startsWith("csrftoken="))
    ?.split("=")[1];
};

const BotIcon = () => (
  <div className="bot-avatar">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 8V4H8" />
      <rect width="16" height="12" x="4" y="8" rx="2" />
      <path d="M2 14h2M20 14h2M9 13v2M15 13v2" />
    </svg>
  </div>
);

export default function Chat({ messages, setMessages, sessionId, pendingFeedback, onFeedbackProcessed, onNewMessageSent, toggleSidebar }) {
  const [message, setMessage] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const bottomRef = useRef(null);
  const textareaRef = useRef(null);
  const feedbackProcessedRef = useRef(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  useEffect(() => {
    if (pendingFeedback && !feedbackProcessedRef.current) {
      if (pendingFeedback !== "ACCEPT" && pendingFeedback !== "REJECT") {
        onFeedbackProcessed();
        return;
      }
      feedbackProcessedRef.current = true;
      const feedbackMessage = pendingFeedback === "ACCEPT"
        ? "I have accepted and authorized the booking."
        : "I have rejected the booking.";
      postChatMessage(feedbackMessage, { silent: false });
      onFeedbackProcessed();
    }
    if (!pendingFeedback) {
      feedbackProcessedRef.current = false;
    }
  }, [pendingFeedback]);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(e);
    }
  };

  const postChatMessage = async (text, { silent = false } = {}) => {
    if (text.trim() === "" || isLoading) return;

    if (!silent) {
      const newUserMessage = { id: Date.now(), message: text, type: "User" };
      setMessages((prev) => [...prev, newUserMessage]);
    }
    setIsLoading(true);

    try {
      const response = await fetch("http://127.0.0.1:8000/ai_agent/chat/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        credentials: "include",
        body: JSON.stringify({ session_id: sessionId, message: text }),
      });

      const data = await response.json();

      if (response.ok) {
        setMessages((prev) => [
          ...prev,
          { id: Date.now() + 1, message: data.agent_response, type: "Bot" },
        ]);
        if (onNewMessageSent) onNewMessageSent();
      } else {
        setMessages((prev) => [
          ...prev,
          { id: Date.now() + 1, message: "Something went wrong. Please try again.", type: "Bot" },
        ]);
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { id: Date.now() + 1, message: "Connection error. Check that the server is running.", type: "Bot" },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const sendMessage = (e) => {
    e.preventDefault();
    if (message.trim() === "" || isLoading) return;
    const currentInput = message;
    setMessage("");
    postChatMessage(currentInput);
  };

  return (
    <div className="chat-shell">
      {/* Header */}
      <header className="chat-header">
        <div className="header-inner">
          <div className="header-left">
            <button className="sidebar-toggle-btn-desktop" onClick={toggleSidebar} aria-label="Toggle Sidebar">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="3" y1="12" x2="21" y2="12"></line>
                <line x1="3" y1="6" x2="21" y2="6"></line>
                <line x1="3" y1="18" x2="21" y2="18"></line>
              </svg>
            </button>
            <span className="header-title">AI Agent</span>
            <span className="session-badge">{sessionId.slice(0, 14)}</span>
          </div>
          <button 
            className="clear-chat-btn"
            onClick={async () => {
              if (window.confirm("Are you sure you want to clear this conversation?")) {
                localStorage.removeItem("cheq_messages");
                setMessages([
                  { id: 1, message: "Hi! How can I help you today?", type: "Bot" },
                ]);
                try {
                  await fetch("http://127.0.0.1:8000/ai_agent/clear_memory/", {
                    method: "POST",
                    headers: {
                      "Content-Type": "application/json",
                      "X-CSRFToken": getCsrfToken(),
                    },
                    body: JSON.stringify({ session_id: sessionId })
                  });
                } catch (e) {
                  // ignore
                }
              }
            }}
            title="Clear Conversation"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6" />
            </svg>
            Clear Chat
          </button>
        </div>
      </header>

      {/* Messages */}
      <main className="chat-messages">
        <div className="messages-inner">
          {messages.map((msg) =>
            msg.type === "Bot" ? (
              <div key={msg.id} className="message-row bot-row">
                <BotIcon />
                <div className="message-content bot-content markdown-body">
                  <ReactMarkdown 
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({node, ...props}) => {
                        const href = props.href || "";
                        if (href.includes("resource_uri=")) {
                          return (
                            <a 
                              {...props}
                              onClick={(e) => {
                                e.preventDefault();
                                try {
                                  const url = new URL(href, window.location.origin);
                                  window.history.pushState({}, '', url.pathname + url.search);
                                } catch (err) {
                                  window.history.pushState({}, '', href);
                                }
                                window.dispatchEvent(new PopStateEvent('popstate'));
                              }}
                            />
                          );
                        }
                        return <a {...props} />;
                      }
                    }}
                  >
                    {msg.message}
                  </ReactMarkdown>
                </div>
              </div>
            ) : (
              <div key={msg.id} className="message-row user-row">
                <div className="message-content user-content">
                  <p>{msg.message}</p>
                </div>
              </div>
            )
          )}

          {isLoading && (
            <div className="message-row bot-row">
              <BotIcon />
              <div className="message-content bot-content">
                <div className="typing-dots">
                  <span /><span /><span />
                </div>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </main>

      {/* Input */}
      <footer className="chat-footer">
        <div className="input-wrapper">
          <textarea
            ref={textareaRef}
            className="chat-input"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Message AI Agent..."
            rows={1}
          />
          <button
            className={`send-btn ${message.trim() && !isLoading ? "send-btn--active" : ""}`}
            onClick={sendMessage}
            disabled={!message.trim() || isLoading}
            aria-label="Send message"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
          </button>
        </div>
        <p className="input-hint">Enter to send · Shift+Enter for new line</p>
      </footer>
    </div>
  );
}