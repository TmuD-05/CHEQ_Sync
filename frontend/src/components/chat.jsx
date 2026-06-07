import React, { useState } from "react";

const getCsrfToken = () => {
  return document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1];
};
export default function Chat() 
{
  const [messages, setMessages] = useState([
    { id: 1, message: "Hi! How can I help you?", type: "Bot" },
  ]);
  const [message, setMessage] = useState("");

  const [sessionId] = useState(`session_${Math.random().toString(36).substr(2, 9)}`);

  const sendMessage = async (e) => {
    e.preventDefault();
    if (message.trim() === "") return;

    const newUserMessage = { id: Date.now(), message: message, type: "User" };
    setMessages((prev) => [...prev, newUserMessage]);
    
    const currentInput = message;
    setMessage("");

    try {
     
      const response = await fetch("http://127.0.0.1:8000/ai_agent/chat/", {
        method: "POST",
        headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),  
      },
        credentials: "include", 
        body: JSON.stringify({
          session_id: sessionId,
          message: currentInput,}),
      });
      const data = await response.json();

      if (response.ok) {
      
        const newBotMessage = {
          id: Date.now() + 1,
          message: data.agent_response, 
          type: "Bot",
        };
        setMessages((prev) => [...prev, newBotMessage]);
      } else {
        console.error("Backend Error:", data.error);
      }
    } catch (error) {
      console.error("Connection Error:", error);
    }
  };

  // const clearMessages = async () => {
  
  //   await fetch("http://127.0.0.1:8000/ai_agent/clear_memory/", {
  //   method: "POST",
  //   headers: { 
  //     "Content-Type": "application/json",
  //     "X-CSRFToken": getCsrfToken(),  
  //   },
  //     credentials: "include",  
  //     body: JSON.stringify({ session_id: sessionId })
  //   });
  // }

  return (
    <div>
      <header><h1>AI Agent Chat</h1></header>
      <section style={{ height: "400px", overflowY: "scroll", border: "1px solid #ccc" }}>
        {messages.map((msg) => (
          <div key={msg.id} style={{ margin: "10px", textAlign: msg.type === "User" ? "right" : "left" }}>
            <strong>{msg.type}:</strong> {msg.message}
          </div>
        ))}
      </section>
      <form onSubmit={sendMessage}>
        <input value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Ask about processes..." />
        <button type="submit">Send</button>
        {/* <button type="button" onClick={clearMessages}>Clear</button> */}
      </form>
    </div>
  );
}
