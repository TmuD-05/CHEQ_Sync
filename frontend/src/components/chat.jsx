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

export default function Chat() {
  const [messages, setMessages] = useState([
    { id: 1, message: "Hi! How can I help you today?", type: "Bot" },
  ]);
  const [message, setMessage] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(`session_${Math.random().toString(36).substr(2, 9)}`);
  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(e);
    }
  };

  const sendMessage = async (e) => {
    e.preventDefault();
    if (message.trim() === "" || isLoading) return;

    const newUserMessage = { id: Date.now(), message: message, type: "User" };
    setMessages((prev) => [...prev, newUserMessage]);
    const currentInput = message;
    setMessage("");
    setIsLoading(true);

    try {
      const response = await fetch("http://127.0.0.1:8000/ai_agent/chat/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        credentials: "include",
        body: JSON.stringify({ session_id: sessionId, message: currentInput }),
      });

      const data = await response.json();

      if (response.ok) {
        setMessages((prev) => [
          ...prev,
          { id: Date.now() + 1, message: data.agent_response, type: "Bot" },
        ]);
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

  return (
    <div className="chat-shell">
      {/* Header */}
      <header className="chat-header">
        <div className="header-inner">
          <span className="header-title">AI Agent</span>
          <span className="session-badge">{sessionId.slice(0, 14)}</span>
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
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
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