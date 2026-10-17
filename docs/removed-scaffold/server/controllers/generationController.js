import Generation from '../models/Generation.js';
import Asset from '../models/Asset.js';
import Project from '../models/Project.js';
import imageGenerationService from '../services/imageGeneration.js';

export const createGeneration = async (req, res) => {
  try {
    const { prompt, negativePrompt, model, parameters, projectId } = req.body;

    // Validate project exists and user has access
    const project = await Project.findOne({
      _id: projectId,
      owner: req.user.id,
    });

    if (!project) {
      return res.status(404).json({ error: 'Project not found' });
    }

    // Check quota
    if (req.user.quota.computeUnits.used >= req.user.quota.computeUnits.total) {
      return res.status(403).json({ error: 'Compute quota exceeded' });
    }

    let result;
    if (model === 'flux-1.1-pro') {
      result = await imageGenerationService.generateWithFlux(
        prompt,
        parameters,
        req.user.id,
        projectId
      );
    } else if (model === 'sdxl-turbo') {
      result = await imageGenerationService.generateWithSDXL(
        prompt,
        parameters,
        req.user.id,
        projectId
      );
    } else {
      return res.status(400).json({ error: 'Invalid model specified' });
    }

    // Update project
    project.generations.push(result.generation._id);
    project.assets.push(result.asset._id);
    project.computeUsage += result.generation.computeCost;
    await project.save();

    // Update user quota
    req.user.quota.computeUnits.used += result.generation.computeCost;
    await req.user.save();

    res.status(201).json({
      generation: result.generation,
      asset: result.asset,
    });
  } catch (error) {
    console.error('Generation error:', error);
    res.status(500).json({ error: error.message });
  }
};

export const batchGenerate = async (req, res) => {
  try {
    const { jobs, projectId } = req.body;

    const project = await Project.findOne({
      _id: projectId,
      owner: req.user.id,
    });

    if (!project) {
      return res.status(404).json({ error: 'Project not found' });
    }

    const results = await imageGenerationService.batchGenerate(
      jobs,
      req.user.id,
      projectId
    );

    res.status(201).json({ results });
  } catch (error) {
    console.error('Batch generation error:', error);
    res.status(500).json({ error: error.message });
  }
};

export const getGenerations = async (req, res) => {
  try {
    const { projectId, status, limit = 50, page = 1 } = req.query;

    const query = { user: req.user.id };
    if (projectId) query.project = projectId;
    if (status) query.status = status;

    const generations = await Generation.find(query)
      .sort({ createdAt: -1 })
      .limit(parseInt(limit))
      .skip((page - 1) * limit)
      .populate('assetIds');

    const total = await Generation.countDocuments(query);

    res.json({
      generations,
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

export const getGenerationById = async (req, res) => {
  try {
    const generation = await Generation.findOne({
      _id: req.params.id,
      user: req.user.id,
    }).populate('assetIds');

    if (!generation) {
      return res.status(404).json({ error: 'Generation not found' });
    }

    res.json(generation);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};

export const cancelGeneration = async (req, res) => {
  try {
    const generation = await Generation.findOne({
      _id: req.params.id,
      user: req.user.id,
    });

    if (!generation) {
      return res.status(404).json({ error: 'Generation not found' });
    }

    if (generation.status === 'completed' || generation.status === 'failed') {
      return res.status(400).json({ error: 'Cannot cancel completed generation' });
    }

    generation.status = 'failed';
    generation.error = 'Cancelled by user';
    await generation.save();

    res.json({ message: 'Generation cancelled', generation });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};
