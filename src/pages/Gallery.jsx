import React, { useState, useEffect } from 'react';
import { useProject } from '../context/ProjectContext';
import api from '../services/api';

const Gallery = () => {
  const { currentProject } = useProject();
  const [assets, setAssets] = useState([]);
  const [selectedAsset, setSelectedAsset] = useState(null);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState({
    fileType: '',
    sortBy: 'createdAt',
    order: 'desc',
  });

  useEffect(() => {
    if (currentProject) {
      fetchAssets();
    }
  }, [currentProject, filters]);

  const fetchAssets = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        projectId: currentProject._id,
        ...filters,
      });
      const response = await api.get(`/assets?${params}`);
      setAssets(response.data.assets);
      if (response.data.assets.length > 0 && !selectedAsset) {
        setSelectedAsset(response.data.assets[0]);
      }
    } catch (error) {
      console.error('Error fetching assets:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex h-full">
      {/* Main Gallery Area */}
      <main className="flex-1 overflow-y-auto custom-scrollbar p-4 canvas-dots">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md font-semibold text-on-surface">
            Asset Library
          </h2>
          <div className="flex items-center gap-2">
            <select
              value={filters.sortBy}
              onChange={(e) => setFilters({ ...filters, sortBy: e.target.value })}
              className="h-8 px-3 rounded bg-surface-container border border-outline-variant text-on-surface font-body-sm text-body-sm focus:outline-none focus:border-primary transition-colors-150"
            >
              <option value="createdAt">Newest First</option>
              <option value="quality">Highest Quality</option>
              <option value="exportCount">Most Exported</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center h-64">
            <div className="w-12 h-12 border-4 border-primary border-t-transparent rounded-full spinner-fast"></div>
          </div>
        ) : (
          <div className="columns-1 md:columns-2 xl:columns-3 gap-4">
            {assets.map((asset, idx) => (
              <div
                key={asset._id}
                onClick={() => setSelectedAsset(asset)}
                className={`break-inside-avoid mb-4 group relative rounded-lg bg-surface-container border overflow-hidden cursor-pointer stagger-item transition-base ${
                  selectedAsset?._id === asset._id
                    ? 'border-primary card-active-glow'
                    : 'border-outline-variant hover:border-outline'
                }`}
                style={{ animationDelay: `${idx * 30}ms` }}
              >
                <div className="relative w-full aspect-video overflow-hidden bg-surface-container-lowest">
                  <img
                    src={asset.url}
                    alt={asset.originalName}
                    className="w-full h-full object-cover img-fade-in hover-scale"
                    onError={(e) => {
                      e.target.src = 'https://via.placeholder.com/800x600?text=Asset';
                    }}
                  />
                  {asset.quality && (
                    <div className="absolute top-2 left-2 px-2 py-0.5 rounded bg-surface-container-lowest/80 backdrop-blur border border-outline-variant text-emerald-400 font-code-sm text-code-sm flex items-center gap-1">
                      <span className="material-symbols-outlined text-xs">star</span>
                      <span>{asset.quality.toFixed(1)} / 10</span>
                    </div>
                  )}
                </div>
                <div className="p-3 bg-surface-container-low border-t border-outline-variant">
                  <h3 className="font-body-base text-body-base font-semibold text-on-surface truncate">
                    {asset.originalName || asset.filename}
                  </h3>
                  <div className="flex items-center justify-between font-code-sm text-code-sm text-outline mt-1">
                    <span>{asset.aspectRatio}</span>
                    <span>{(asset.size / 1000000).toFixed(1)} MB</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {!loading && assets.length === 0 && (
          <div className="flex flex-col items-center justify-center h-64 text-center stagger-item">
            <span className="material-symbols-outlined text-5xl text-outline mb-4">
              photo_library
            </span>
            <p className="font-body-base text-body-base text-on-surface-variant">
              No assets found. Start generating images to populate your gallery.
            </p>
          </div>
        )}
      </main>

      {/* Right Inspector Panel */}
      {selectedAsset && (
        <aside className="w-96 border-l border-outline-variant bg-surface-container-lowest shrink-0 flex flex-col overflow-y-auto custom-scrollbar modal-enter">
          <div className="p-3.5 border-b border-outline-variant flex items-center justify-between bg-surface-container-low">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-base text-primary">info</span>
              <span className="font-title-sm text-title-sm font-semibold text-on-surface">
                Asset Inspector
              </span>
            </div>
          </div>

          <div className="p-4 flex-1 flex flex-col gap-4">
            <div className="relative rounded-lg border border-outline-variant overflow-hidden bg-surface-container-lowest">
              <div className="aspect-video w-full relative">
                <img
                  src={selectedAsset.url}
                  alt={selectedAsset.originalName}
                  className="w-full h-full object-cover img-fade-in"
                  onError={(e) => {
                    e.target.src = 'https://via.placeholder.com/800x600?text=Asset';
                  }}
                />
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <h2 className="font-headline-md text-title-sm font-bold text-on-surface leading-snug">
                {selectedAsset.originalName || selectedAsset.filename}
              </h2>
            </div>

            <div className="flex flex-col gap-2 pt-1 pb-2 border-y border-outline-variant">
              <button className="h-9 rounded bg-primary text-on-primary font-title-sm text-body-sm font-semibold flex items-center justify-center gap-1.5 shadow-md shadow-primary/30 transition-transform-200 active:scale-[0.97] hover:brightness-110">
                <span className="material-symbols-outlined text-base">download</span>
                <span>Download File</span>
              </button>
            </div>

            <div className="flex flex-col gap-3">
              <span className="font-code-sm text-code-sm text-outline uppercase tracking-wider">
                File Specifications
              </span>
              <div className="grid grid-cols-2 gap-2 text-body-sm">
                <div className="p-2 rounded bg-surface-container border border-outline-variant/50 flex flex-col">
                  <span className="font-param-label text-param-label text-outline">Format</span>
                  <span className="font-param-value text-param-value text-on-surface">
                    {selectedAsset.mimeType?.split('/')[1]?.toUpperCase() || 'PNG'}
                  </span>
                </div>
                <div className="p-2 rounded bg-surface-container border border-outline-variant/50 flex flex-col">
                  <span className="font-param-label text-param-label text-outline">
                    Resolution
                  </span>
                  <span className="font-param-value text-param-value text-on-surface">
                    {selectedAsset.width} x {selectedAsset.height}
                  </span>
                </div>
                <div className="p-2 rounded bg-surface-container border border-outline-variant/50 flex flex-col">
                  <span className="font-param-label text-param-label text-outline">
                    Aspect Ratio
                  </span>
                  <span className="font-param-value text-param-value text-on-surface">
                    {selectedAsset.aspectRatio}
                  </span>
                </div>
                <div className="p-2 rounded bg-surface-container border border-outline-variant/50 flex flex-col">
                  <span className="font-param-label text-param-label text-outline">
                    File Size
                  </span>
                  <span className="font-param-value text-param-value text-on-surface">
                    {(selectedAsset.size / 1000000).toFixed(2)} MB
                  </span>
                </div>
              </div>

              {selectedAsset.metadata?.prompt && (
                <div className="flex flex-col gap-1.5 mt-2">
                  <span className="font-code-sm text-code-sm text-outline uppercase tracking-wider">
                    Prompt Payload
                  </span>
                  <div className="p-2.5 rounded bg-surface-container-lowest border border-outline-variant font-code-sm text-code-sm text-on-surface-variant leading-relaxed">
                    {selectedAsset.metadata.prompt}
                  </div>
                </div>
              )}

              {selectedAsset.tags && selectedAsset.tags.length > 0 && (
                <div className="flex flex-col gap-1.5 mt-1">
                  <span className="font-code-sm text-code-sm text-outline uppercase tracking-wider">
                    Tags
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {selectedAsset.tags.map((tag, idx) => (
                      <span
                        key={idx}
                        className="px-2 py-0.5 rounded-full bg-primary/10 border border-primary/30 text-primary font-code-sm text-code-sm"
                      >
                        #{tag}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </aside>
      )}
    </div>
  );
};

export default Gallery;
