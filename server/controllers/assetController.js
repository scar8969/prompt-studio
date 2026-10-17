import Asset from '../models/Asset.js';
import Project from '../models/Project.js';

export const getAssets = async (req, res) => {
  try {
    const {
      projectId,
      fileType,
      tags,
      minQuality,
      sortBy = 'createdAt',
      order = 'desc',
      limit = 50,
      page = 1,
    } = req.query;

    const query = { user: req.user.id, status: 'ready' };
    if (projectId) query.project = projectId;
    if (fileType) query.fileType = fileType;
    if (tags) query.tags = { $in: tags.split(',') };
    if (minQuality) query.quality = { $gte: parseFloat(minQuality) };

    const sortOrder = order === 'desc' ? -1 : 1;
    const sortOptions = { [sortBy]: sortOrder };

    const assets = await Asset.find(query)
      .sort(sortOptions)
      .limit(parseInt(limit))
      .skip((page - 1) * limit)
      .populate('generation', 'jobId model parameters');

    const total = await Asset.countDocuments(query);

    res.json({
      assets,
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

export const getAssetById = async (req, res) => {
  try {
    const asset = await Asset.findOne({
      _id: req.params.id,
      user: req.user.id,
    })
      .populate('generation')
      .populate('variations');

    if (!asset) {
      return res.status(404).json({ error: 'Asset not found' });
    }

    res.json(asset);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};

export const updateAsset = async (req, res) => {
  try {
    const { tags, quality, metadata } = req.body;

    const asset = await Asset.findOne({
      _id: req.params.id,
      user: req.user.id,
    });

    if (!asset) {
      return res.status(404).json({ error: 'Asset not found' });
    }

    if (tags) asset.tags = tags;
    if (quality !== undefined) asset.quality = quality;
    if (metadata) asset.metadata = { ...asset.metadata, ...metadata };

    await asset.save();
    res.json(asset);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};

export const deleteAsset = async (req, res) => {
  try {
    const asset = await Asset.findOneAndDelete({
      _id: req.params.id,
      user: req.user.id,
    });

    if (!asset) {
      return res.status(404).json({ error: 'Asset not found' });
    }

    // Remove from project
    await Project.updateOne(
      { _id: asset.project },
      { $pull: { assets: asset._id } }
    );

    res.json({ message: 'Asset deleted successfully' });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};

export const incrementExport = async (req, res) => {
  try {
    const asset = await Asset.findOne({
      _id: req.params.id,
      user: req.user.id,
    });

    if (!asset) {
      return res.status(404).json({ error: 'Asset not found' });
    }

    asset.exportCount += 1;
    await asset.save();

    res.json({ exportCount: asset.exportCount });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};

export const getTopAssets = async (req, res) => {
  try {
    const { projectId, limit = 10 } = req.query;

    const query = { user: req.user.id, status: 'ready' };
    if (projectId) query.project = projectId;

    const assets = await Asset.find(query)
      .sort({ quality: -1, exportCount: -1 })
      .limit(parseInt(limit));

    res.json(assets);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
};
