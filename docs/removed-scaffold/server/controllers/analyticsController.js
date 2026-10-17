import User from '../models/User.js';
import Generation from '../models/Generation.js';
import Asset from '../models/Asset.js';
import Project from '../models/Project.js';

export const getAnalytics = async (req, res) => {
  try {
    const { period = '7d', projectId } = req.query;

    const now = new Date();
    const periodMs = {
      '7d': 7 * 24 * 60 * 60 * 1000,
      '30d': 30 * 24 * 60 * 60 * 1000,
      '90d': 90 * 24 * 60 * 60 * 1000,
    };

    const startDate = new Date(now - (periodMs[period] || periodMs['7d']));

    const query = { user: req.user.id, createdAt: { $gte: startDate } };
    if (projectId) query.project = projectId;

    // Total generations
    const totalGenerations = await Generation.countDocuments(query);

    // Completed generations
    const completedGenerations = await Generation.countDocuments({
      ...query,
      status: 'completed',
    });

    // Average latency
    const avgLatencyResult = await Generation.aggregate([
      { $match: { ...query, status: 'completed' } },
      { $group: { _id: null, avgDuration: { $avg: '$duration' } } },
    ]);

    // Success rate
    const successRate = totalGenerations
      ? ((completedGenerations / totalGenerations) * 100).toFixed(2)
      : 100;

    // Total compute usage
    const computeUsage = await Generation.aggregate([
      { $match: query },
      { $group: { _id: null, total: { $sum: '$computeCost' } } },
    ]);

    // Generation volume over time
    const volumeData = await Generation.aggregate([
      { $match: query },
      {
        $group: {
          _id: {
            $dateToString: { format: '%Y-%m-%d', date: '$createdAt' },
          },
          count: { $sum: 1 },
        },
      },
      { $sort: { _id: 1 } },
    ]);

    // Model usage breakdown
    const modelUsage = await Generation.aggregate([
      { $match: query },
      {
        $group: {
          _id: '$model',
          count: { $sum: 1 },
          computeUsed: { $sum: '$computeCost' },
        },
      },
    ]);

    // User quota
    const user = await User.findById(req.user.id);
    const quotaUsage = {
      used: user.quota.computeUnits.used,
      total: user.quota.computeUnits.total,
      percentage: (
        (user.quota.computeUnits.used / user.quota.computeUnits.total) *
        100
      ).toFixed(2),
    };

    // Active workers (mock)
    const activeWorkers = Math.floor(Math.random() * 4) + 10;

    res.json({
      overview: {
        totalGenerations,
        completedGenerations,
        successRate: parseFloat(successRate),
        avgLatency: avgLatencyResult[0]?.avgDuration || 4800,
        activeWorkers,
      },
      quota: quotaUsage,
      computeUsage: computeUsage[0]?.total || 0,
      volumeData,
      modelUsage,
      period,
    });
  } catch (error) {
    console.error('Analytics error:', error);
    res.status(500).json({ error: error.message });
  }
};

export const getQuotaStatus = async (req, res) => {
  try {
    const user = await User.findById(req.user.id);

    const quotaInfo = {
      computeUnits: {
        used: user.quota.computeUnits.used,
        total: user.quota.computeUnits.total,
        remaining: user.quota.computeUnits.total - user.quota.computeUnits.used,
        percentage: (
          (user.quota.computeUnits.used / user.quota.computeUnits.total) *
          100
        ).toFixed(2),
      },
      resetDate: user.quota.resetDate,
      tier: user.role,
    };

    res.json(quotaInfo);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};
