import React, { useState, useEffect, useRef } from 'react';
import { useProject } from '../context/ProjectContext';
import api from '../services/api';

const ChatStudio = () => {
  const { currentProject } = useProject();
  const [prompt, setPrompt] = useState('');
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [generations, setGenerations] = useState([]);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    if (currentProject) {
      fetchGenerations();
    }
  }, [currentProject]);

  useEffect(() => {
    // Scroll to bottom when messages change
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const fetchGenerations = async () => {
    try {
      const response = await api.get(`/generations?projectId=${currentProject._id}&limit=10`);
      setGenerations(response.data.generations);
    } catch (error) {
      console.error('Error fetching generations:', error);
    }
  };

  const handleGenerate = async () => {
    if (!prompt.trim() || !currentProject) return;

    setLoading(true);
    const userMessage = { type: 'user', content: prompt };
    const loadingMessage = { type: 'ai', content: 'Processing your request...', loading: true };

    setMessages([...messages, userMessage, loadingMessage]);
    const currentPrompt = prompt;
    setPrompt('');

    try {
      const response = await api.post('/generations', {
        prompt: currentPrompt,
        model: 'flux-1.1-pro',
        parameters: {
          width: 1024,
          height: 1024,
          steps: 30,
          cfgScale: 3.5,
        },
        projectId: currentProject._id,
      });

      setMessages(prev => [
        ...prev.slice(0, -1), // Remove loading message
        { type: 'ai', content: 'Generation completed!', generation: response.data }
      ]);
      fetchGenerations();
    } catch (error) {
      setMessages(prev => [
        ...prev.slice(0, -1), // Remove loading message
        { type: 'ai', content: `Error: ${error.response?.data?.error || 'Generation failed'}`, error: true }
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-surface-dim">
      <div className="px-6 py-2 border-b border-outline-variant bg-surface-container-lowest/70 backdrop-blur flex items-center justify-between sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <span className="font-code-sm text-code-sm text-outline uppercase tracking-wider">
            Active Pipeline
          </span>
          <div className="flex items-center gap-1.5 bg-surface-container px-2 py-0.5 rounded border border-outline-variant font-code-sm text-code-sm text-on-surface">
            <span className="w-1.5 h-1.5 rounded-full bg-tertiary"></span>
            <span>Logic: anthropic/claude-3-5-sonnet</span>
          </div>
          <div className="flex items-center gap-1.5 bg-surface-container px-2 py-0.5 rounded border border-outline-variant font-code-sm text-code-sm text-on-surface">
            <span className="w-1.5 h-1.5 rounded-full bg-primary"></span>
            <span>Image: black-forest-labs/flux-1.1-pro</span>
          </div>
        </div>
      </div>

      <div className="flex-1 p-6 space-y-6 max-w-5xl mx-auto w-full pb-36 overflow-y-auto custom-scrollbar">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center stagger-item">
            <div className="w-16 h-16 rounded-full bg-surface-container border border-outline-variant flex items-center justify-center mb-4">
              <span className="material-symbols-outlined text-3xl text-primary">auto_awesome</span>
            </div>
            <h2 className="font-headline-md text-headline-md font-semibold text-on-surface mb-2">
              Start Your Creative Journey
            </h2>
            <p className="font-body-base text-body-base text-on-surface-variant max-w-md">
              Describe your vision, and our AI will generate stunning visuals using Flux 1.1 Pro
              Editorial engine.
            </p>
          </div>
        )}

        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={`flex gap-3 ${msg.type === 'user' ? 'justify-end' : 'justify-start'} items-start message-enter`}
            style={{ animationDelay: `${idx * 50}ms` }}
          >
            {msg.type === 'ai' && (
              <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary-container to-secondary-container flex items-center justify-center text-white shrink-0 shadow-md">
                <span className={`material-symbols-outlined text-sm ${msg.loading ? 'animate-spin' : ''}`}>
                  auto_awesome
                </span>
              </div>
            )}
            <div
              className={`max-w-2xl ${
                msg.type === 'user'
                  ? 'bg-surface-container-high'
                  : msg.error
                  ? 'bg-error-container border-error'
                  : 'bg-surface-container-low'
              } border border-outline-variant rounded-xl p-4 shadow-sm`}
            >
              <p className={`font-body-base text-body-base ${msg.error ? 'text-on-error-container' : 'text-on-surface'} leading-relaxed`}>
                {msg.content}
              </p>
              {msg.generation?.asset && (
                <div className="mt-3 rounded-lg overflow-hidden border border-outline-variant">
                  <img
                    src={msg.generation.asset.url}
                    alt="Generated"
                    className="w-full h-auto img-fade-in"
                    loading="lazy"
                    onError={(e) => {
                      e.target.src = 'https://via.placeholder.com/512x512?text=Generated+Image';
                    }}
                  />
                </div>
              )}
            </div>
            {msg.type === 'user' && (
              <div className="w-8 h-8 rounded-full bg-surface-container-highest flex items-center justify-center text-on-surface font-semibold text-xs border border-outline shrink-0">
                CD
              </div>
            )}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="absolute bottom-4 left-6 right-6 max-w-5xl mx-auto z-20">
        <div className="bg-surface-container-low/95 backdrop-blur-xl border border-outline-variant focus-within:border-primary rounded-xl shadow-2xl p-3 space-y-2.5 transition-colors-150">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleGenerate();
              }
            }}
            className="w-full bg-transparent border-0 text-on-surface font-body-base text-body-base placeholder:text-outline focus:ring-0 focus:outline-none resize-none p-1"
            placeholder="Instruct the AI planner, describe image concepts, or paste direct prompts... (Enter to send, Shift+Enter for newline)"
            rows="2"
            disabled={loading}
          />
          <div className="flex items-center justify-end gap-3 pt-1 border-t border-outline-variant/60">
            <button
              onClick={handleGenerate}
              disabled={loading || !prompt.trim()}
              className="h-8 px-3.5 rounded-lg bg-gradient-to-r from-primary-container to-secondary-container text-white font-body-sm text-body-sm font-semibold flex items-center gap-1.5 hover:brightness-110 shadow-md shadow-primary-container/30 transition-transform-200 active:scale-[0.97] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span className="material-symbols-outlined text-sm">bolt</span>
              <span>Send & Plan</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatStudio;
