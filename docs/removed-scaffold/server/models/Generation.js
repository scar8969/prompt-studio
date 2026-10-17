import mongoose from 'mongoose';

const generationSchema = new mongoose.Schema(
  {
    jobId: {
      type: String,
      unique: true,
      required: true,
    },
    prompt: {
      type: String,
      required: [true, 'Prompt is required'],
    },
    negativePrompt: String,
    model: {
      type: String,
      required: true,
      enum: ['flux-1.1-pro', 'sdxl-turbo', 'midjourney-v6'],
    },
    parameters: {
      width: { type: Number, default: 1024 },
      height: { type: Number, default: 1024 },
      steps: { type: Number, default: 30 },
      cfgScale: { type: Number, default: 3.5 },
      seed: Number,
      sampler: String,
    },
    status: {
      type: String,
      enum: ['queued', 'processing', 'completed', 'failed', 'retrying'],
      default: 'queued',
    },
    progress: {
      type: Number,
      default: 0,
      min: 0,
      max: 100,
    },
    outputUrls: [String],
    assetIds: [
      {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Asset',
      },
    ],
    computeCost: {
      type: Number,
      default: 0,
    },
    duration: Number,
    error: String,
    retryCount: {
      type: Number,
      default: 0,
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

// Index for efficient queries
generationSchema.index({ project: 1, status: 1 });
generationSchema.index({ user: 1, createdAt: -1 });

export default mongoose.model('Generation', generationSchema);
