import Project from '../models/Project.js';
import Generation from '../models/Generation.js';
import Asset from '../models/Asset.js';

export const createProject = async (req, res) => {
  try {
    const { title, description, tags, modelPreset } = req.body;

    const project = await Project.create({
      title,
      description,
      tags,
      modelPreset,
      owner: req.user.id,
    });

    res.status(201).json(project);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};

export const getProjects = async (req, res) => {
  try {
    const { status, limit = 20, page = 1 } = req.query;

    const query = { owner: req.user.id };
    if (status) query.status = status;

    const projects = await Project.find(query)
      .sort({ updatedAt: -1 })
      .limit(parseInt(limit))
      .skip((page - 1) * limit);

    const total = await Project.countDocuments(query);

    res.json({
      projects,
      pagination: {
        page: parseInt(page),
        limit: parseInt(limit),
        total,
        pages: Math.ceil(total / limit),
      },
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};

export const getProjectById = async (req, res) => {
  try {
    const project = await Project.findOne({
      _id: req.params.id,
      owner: req.user.id,
    })
      .populate('assets')
      .populate('generations');

    if (!project) {
      return res.status(404).json({ error: 'Project not found' });
    }

    res.json(project);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};

export const updateProject = async (req, res) => {
  try {
    const { title, description, tags, modelPreset, status } = req.body;

    const project = await Project.findOne({
      _id: req.params.id,
      owner: req.user.id,
    });

    if (!project) {
      return res.status(404).json({ error: 'Project not found' });
    }

    if (title) project.title = title;
    if (description !== undefined) project.description = description;
    if (tags) project.tags = tags;
    if (modelPreset) project.modelPreset = modelPreset;
    if (status) project.status = status;

    await project.save();
    res.json(project);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};

export const deleteProject = async (req, res) => {
  try {
    const project = await Project.findOneAndDelete({
      _id: req.params.id,
      owner: req.user.id,
    });

    if (!project) {
      return res.status(404).json({ error: 'Project not found' });
    }

    // Clean up related data
    await Generation.deleteMany({ project: project._id });
    await Asset.deleteMany({ project: project._id });

    res.json({ message: 'Project deleted successfully' });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};

export const getProjectAnalytics = async (req, res) => {
  try {
    const project = await Project.findOne({
      _id: req.params.id,
      owner: req.user.id,
    });

    if (!project) {
      return res.status(404).json({ error: 'Project not found' });
    }

    const totalGenerations = await Generation.countDocuments({ project: project._id });
    const completedGenerations = await Generation.countDocuments({
      project: project._id,
      status: 'completed',
    });
    const totalAssets = await Asset.countDocuments({ project: project._id });

    const avgLatency = await Generation.aggregate([
      { $match: { project: project._id, status: 'completed' } },
      { $group: { _id: null, avgDuration: { $avg: '$duration' } } },
    ]);

    const topAssets = await Asset.find({ project: project._id, status: 'ready' })
      .sort({ quality: -1 })
      .limit(3);

    res.json({
      totalGenerations,
      completedGenerations,
      successRate: totalGenerations
        ? ((completedGenerations / totalGenerations) * 100).toFixed(2)
        : 0,
      totalAssets,
      avgLatency: avgLatency[0]?.avgDuration || 0,
      computeUsage: project.computeUsage,
      topAssets,
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};
