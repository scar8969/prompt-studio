import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useProject } from '../context/ProjectContext';

const Sidebar = () => {
  const location = useLocation();
  const { user, logout } = useAuth();
  const { currentProject } = useProject();

  const navItems = [
    { path: '/', icon: 'forum', label: 'Chat Studio' },
    { path: '/gallery', icon: 'photo_library', label: 'Asset Library' },
    { path: '/analytics', icon: 'monitoring', label: 'Analytics & Quota' },
  ];

  return (
    <aside className="w-64 h-screen flex flex-col justify-between p-3 border-r border-outline-variant bg-surface-container-lowest shrink-0">
      <div className="space-y-4">
        <div className="flex items-center justify-between px-2 pt-1 pb-2 border-b border-outline-variant">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-tr from-secondary-container to-primary-container flex items-center justify-center shadow-lg relative">
              <span className="material-symbols-outlined text-primary text-base">
                auto_awesome
              </span>
              <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-tertiary ring-2 ring-surface-container-lowest animate-pulse"></span>
            </div>
            <div>
              <div className="font-title-sm text-title-sm font-semibold text-on-surface tracking-tight leading-tight">
                Latent Studio OS
              </div>
              <div className="font-code-sm text-code-sm text-outline tracking-wider uppercase">
                v2.4 Pro Creative Engine
              </div>
            </div>
          </div>
        </div>

        <nav className="space-y-1">
          <div className="px-2 pb-1 font-param-label text-param-label text-outline uppercase tracking-wider">
            Navigation
          </div>
          {navItems.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg font-param-label text-param-label transition-colors-150 active:scale-[0.98] ${
                location.pathname === item.path
                  ? 'text-primary bg-surface-container font-medium'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-low'
              }`}
            >
              <span className="material-symbols-outlined text-lg">{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          ))}
        </nav>

        {currentProject && (
          <div className="px-2 py-2 rounded-lg bg-surface-container-low border border-outline-variant">
            <div className="font-param-label text-param-label text-outline uppercase tracking-wider text-xs mb-2">
              Active Project
            </div>
            <div className="font-body-sm text-body-sm font-medium text-on-surface truncate">
              {currentProject.title}
            </div>
          </div>
        )}
      </div>

      <div className="space-y-2 pt-2 border-t border-outline-variant">
        <div className="flex items-center justify-between px-2 py-1 rounded bg-surface-container-low border border-outline-variant/60 font-code-sm text-code-sm">
          <div className="flex items-center gap-2 text-outline">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
            <span>PORT :8765</span>
          </div>
        </div>

        <div className="flex items-center gap-2.5 px-2 py-2 rounded-lg bg-surface-container-low border border-outline-variant/50">
          <div className="w-7 h-7 rounded-full bg-primary-container/20 border border-primary/30 flex items-center justify-center text-primary font-code-sm text-code-sm">
            {user?.name?.charAt(0) || 'U'}
          </div>
          <div className="flex-1 overflow-hidden">
            <div className="font-body-sm text-body-sm font-medium text-on-surface truncate">
              {user?.name || 'User'}
            </div>
            <div className="font-code-sm text-code-sm text-outline truncate">
              {user?.email}
            </div>
          </div>
          <button
            onClick={logout}
            className="material-symbols-outlined text-base text-outline hover:text-error transition-colors-150 active:scale-95"
            title="Logout"
          >
            logout
          </button>
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;
