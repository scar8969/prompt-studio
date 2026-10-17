import express from 'express';
import { protect } from '../middleware/auth.js';
import {
  createProject,
  getProjects,
  getProjectById,
  updateProject,
  deleteProject,
  getProjectAnalytics,
} from '../controllers/projectController.js';

const router = express.Router();

router.route('/').post(protect, createProject).get(protect, getProjects);

router
  .route('/:id')
  .get(protect, getProjectById)
  .put(protect, updateProject)
  .delete(protect, deleteProject);

router.get('/:id/analytics', protect, getProjectAnalytics);

export default router;
