import mongoose from 'mongoose';

const projectSchema = new mongoose.Schema(
  {
    title: {
      type: String,
      required: [true, 'Project title is required'],
      trim: true,
      maxlength: [100, 'Title cannot exceed 100 characters'],
    },
    slug: {
      type: String,
      unique: true,
      lowercase: true,
    },
    description: {
      type: String,
      maxlength: [500, 'Description cannot exceed 500 characters'],
    },
    tags: [String],
    modelPreset: {
      logic: {
        type: String,
        default: 'claude-3-5-sonnet',
      },
      diffusion: {
        type: String,
        default: 'flux-1.1-pro',
      },
    },
    assets: [
      {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Asset',
      },
    ],
    generations: [
      {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Generation',
      },
    ],
    status: {
      type: String,
      enum: ['active', 'archived', 'draft'],
      default: 'active',
    },
    computeUsage: {
      type: Number,
      default: 0,
    },
    owner: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'User',
      required: true,
    },
  },
  {
    timestamps: true,
  }
);

// Create slug from title before saving
projectSchema.pre('save', function (next) {
  if (this.isModified('title')) {
    this.slug = this.title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '');
  }
  next();
});

export default mongoose.model('Project', projectSchema);
