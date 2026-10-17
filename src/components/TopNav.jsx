import React from 'react';
import { useLocation } from 'react-router-dom';
import { useProject } from '../context/ProjectContext';

const TopNav = () => {
  const location = useLocation();
  const { currentProject } = useProject();

  const getPageTitle = () => {
    switch (location.pathname) {
      case '/':
        return 'Chat Studio';
      case '/gallery':
        return 'Asset Library';
      case '/analytics':
        return 'Analytics & Quota';
      default:
        return 'Latent Studio';
    }
  };

  return (
    <header className="flex justify-between items-center w-full px-4 h-14 border-b border-outline-variant bg-surface-container-low shrink-0">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-outline text-base">folder_open</span>
          <span className="font-title-sm text-title-sm font-semibold text-on-surface tracking-tight">
            {currentProject?.title || 'Product Campaign 2025'}
          </span>
        </div>
        <span className="text-outline-variant">/</span>
        <div className="flex items-center gap-1.5 bg-surface-container-high px-2.5 py-1 rounded-lg border border-outline-variant">
          <span className="material-symbols-outlined text-tertiary text-xs">tune</span>
          <span className="font-param-label text-param-label text-on-surface font-medium">
            Flux 1.1 Pro Editorial
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button className="h-8 px-2.5 rounded bg-surface-container border border-outline-variant hover:bg-surface-container-high text-on-surface font-body-sm text-body-sm flex items-center gap-1.5 transition-colors-150 active:scale-[0.97]">
          <span className="material-symbols-outlined text-sm">upload_file</span>
          <span className="hidden sm:inline">Upload Asset</span>
        </button>
        <button className="h-8 px-3 rounded bg-primary hover:bg-primary/90 text-on-primary font-title-sm text-body-sm font-semibold flex items-center gap-1.5 shadow-md shadow-primary/30 transition-transform-200 active:scale-[0.97]">
          <span className="material-symbols-outlined text-sm">bolt</span>
          <span>Direct Generate</span>
        </button>
      </div>
    </header>
  );
};

export default TopNav;
