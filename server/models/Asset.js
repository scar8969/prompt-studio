import mongoose from 'mongoose';

const assetSchema = new mongoose.Schema(
  {
    filename: {
      type: String,
      required: true,
    },
    originalName: String,
    fileType: {
      type: String,
      enum: ['image', 'video', 'audio', 'document', '3d-model'],
      required: true,
    },
    mimeType: String,
    size: Number,
    width: Number,
    height: Number,
    aspectRatio: String,
    url: {
      type: String,
      required: true,
    },
    thumbnailUrl: String,
    tags: [String],
    metadata: {
      model: String,
      prompt: String,
      seed: Number,
      parameters: mongoose.Schema.Types.Mixed,
    },
    quality: {
      type: Number,
      min: 0,
      max: 10,
    },
    variations: [
      {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Asset',
      },
    ],
    exportCount: {
      type: Number,
      default: 0,
    },
    status: {
      type: String,
      enum: ['processing', 'ready', 'failed'],
      default: 'processing',
    },
    generation: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'Generation',
    },
    project: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'Project',
      required: true,
    },
    user: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'User',
      required: true,
    },
  },
  {
    timestamps: true,
  }
);

// Index for gallery queries
assetSchema.index({ project: 1, createdAt: -1 });
assetSchema.index({ tags: 1 });
assetSchema.index({ quality: -1 });

export default mongoose.model('Asset', assetSchema);
