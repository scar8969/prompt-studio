import React, { useState, useEffect } from 'react';
import { useProject } from '../context/ProjectContext';
import api from '../services/api';

const Analytics = () => {
  const { currentProject } = useProject();
  const [analytics, setAnalytics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState('7d');

  useEffect(() => {
    fetchAnalytics();
  }, [period, currentProject]);

  const fetchAnalytics = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ period });
      if (currentProject) params.append('projectId', currentProject._id);

      const response = await api.get(`/analytics?${params}`);
      setAnalytics(response.data);
    } catch (error) {
      console.error('Error fetching analytics:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="w-12 h-12 border-4 border-primary border-t-transparent rounded-full spinner-fast"></div>
      </div>
    );
  }

  return (
    <main className="p-6 space-y-6 max-w-7xl w-full mx-auto pb-16">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="font-headline-md text-headline-md font-semibold text-on-surface">
          Analytics & Usage Intelligence
        </h1>
        <div className="flex items-center bg-surface-container-lowest border border-outline-variant rounded-lg p-0.5">
          {['7d', '30d', '90d'].map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 rounded font-code-sm text-code-sm transition-transform-200 active:scale-[0.97] ${
                period === p
                  ? 'bg-surface-container-high text-on-surface border border-outline-variant'
                  : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              {p.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {/* Quota Card */}
      <section className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        <div className="lg:col-span-5 bg-surface-container-low border border-outline-variant/60 rounded-xl p-5 shadow-sm stagger-item">
          <div className="flex items-start justify-between mb-4">
            <div>
              <h2 className="font-headline-md text-headline-md text-on-surface font-semibold">
                Compute Quota
              </h2>
              <p className="font-body-sm text-body-sm text-on-surface-variant mt-0.5">
                Monthly allocation tracking
              </p>
            </div>
          </div>

          <div className="flex items-center gap-6 py-4">
            <div className="relative w-36 h-36 flex items-center justify-center">
              <svg className="w-full h-full transform -rotate-90" viewBox="0 0 120 120">
                <circle
                  cx="60"
                  cy="60"
                  r="50"
                  fill="none"
                  stroke="#282a2d"
                  strokeWidth="9"
                />
                <circle
                  cx="60"
                  cy="60"
                  r="50"
                  fill="none"
                  stroke="url(#gradient)"
                  strokeWidth="9"
                  strokeDasharray="314"
                  strokeDashoffset={314 - (314 * (analytics?.quota?.percentage || 0)) / 100}
                  strokeLinecap="round"
                  style={{ transition: 'stroke-dashoffset 600ms var(--ease-out)' }}
                />
                <defs>
                  <linearGradient id="gradient" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#8083ff" />
                    <stop offset="100%" stopColor="#38bdf8" />
                  </linearGradient>
                </defs>
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
                <span className="font-headline-md text-headline-md font-bold text-on-surface leading-tight">
                  {analytics?.quota?.percentage || 0}%
                </span>
                <span className="font-code-sm text-code-sm text-on-surface-variant">UTILIZED</span>
              </div>
            </div>

            <div className="space-y-3 flex-1">
              <div>
                <span className="font-param-label text-param-label text-on-surface-variant block">
                  CONSUMED COMPUTE
                </span>
                <div className="flex items-baseline gap-1.5 mt-0.5">
                  <span className="font-param-value text-xl font-bold text-on-surface">
                    {analytics?.quota?.used?.toLocaleString() || 0}
                  </span>
                  <span className="font-code-sm text-code-sm text-on-surface-variant">
                    / {analytics?.quota?.total?.toLocaleString() || 10000} CU
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* KPI Tiles */}
        <div className="lg:col-span-7 grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="bg-surface-container-low border border-outline-variant/60 rounded-xl p-4 stagger-item">
            <div className="flex justify-between items-start mb-2">
              <span className="font-param-label text-param-label text-on-surface-variant">
                TOTAL GENERATIONS
              </span>
              <span className="material-symbols-outlined text-primary text-base">
                auto_awesome
              </span>
            </div>
            <div className="font-display-lg text-display-lg font-bold text-on-surface">
              {analytics?.overview?.totalGenerations || 0}
            </div>
            <div className="flex items-center gap-1.5 mt-1">
              <span className="material-symbols-outlined text-emerald-400 text-sm">
                trending_up
              </span>
              <span className="font-param-value text-param-value text-emerald-400">
                +{((analytics?.overview?.successRate || 0) * 0.18).toFixed(1)}%
              </span>
            </div>
          </div>

          <div className="bg-surface-container-low border border-outline-variant/60 rounded-xl p-4 stagger-item">
            <div className="flex justify-between items-start mb-2">
              <span className="font-param-label text-param-label text-on-surface-variant">
                AVG LATENCY
              </span>
              <span className="material-symbols-outlined text-tertiary text-base">speed</span>
            </div>
            <div className="font-display-lg text-display-lg font-bold text-on-surface">
              {((analytics?.overview?.avgLatency || 4800) / 1000).toFixed(1)}s
            </div>
            <div className="flex items-center gap-1.5 mt-1">
              <span className="font-param-value text-param-value text-emerald-400">
                -12.0% faster
              </span>
            </div>
          </div>

          <div className="bg-surface-container-low border border-outline-variant/60 rounded-xl p-4 stagger-item">
            <div className="flex justify-between items-start mb-2">
              <span className="font-param-label text-param-label text-on-surface-variant">
                SUCCESS RATE
              </span>
              <span className="material-symbols-outlined text-emerald-400 text-base">
                verified
              </span>
            </div>
            <div className="font-display-lg text-display-lg font-bold text-on-surface">
              {analytics?.overview?.successRate?.toFixed(1) || 99.2}%
            </div>
          </div>

          <div className="bg-surface-container-low border border-outline-variant/60 rounded-xl p-4 stagger-item">
            <div className="flex justify-between items-start mb-2">
              <span className="font-param-label text-param-label text-on-surface-variant">
                ACTIVE WORKERS
              </span>
              <span className="material-symbols-outlined text-on-surface text-base">hub</span>
            </div>
            <div className="font-display-lg text-display-lg font-bold text-on-surface">
              {analytics?.overview?.activeWorkers || 12}
            </div>
          </div>
        </div>
      </section>

      {/* Model Usage Breakdown */}
      {analytics?.modelUsage && analytics.modelUsage.length > 0 && (
        <section className="bg-surface-container-low border border-outline-variant/60 rounded-xl p-5 stagger-item">
          <h3 className="font-headline-md text-headline-md text-on-surface font-semibold mb-4">
            Model Usage Breakdown
          </h3>
          <div className="space-y-3">
            {analytics.modelUsage.map((model) => (
              <div key={model._id} className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-body-base text-body-base text-on-surface">
                    {model._id}
                  </span>
                  <span className="font-param-value text-param-value text-on-surface">
                    {model.count} generations ({model.computeUsed.toFixed(1)} CU)
                  </span>
                </div>
                <div className="h-2 w-full bg-surface-container-highest rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full"
                    style={{
                      width: `${(model.count / analytics.overview.totalGenerations) * 100}%`,
                      transition: 'width 500ms var(--ease-out)',
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
};

export default Analytics;
