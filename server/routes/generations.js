import express from 'express';
import { protect } from '../middleware/auth.js';
import {
  createGeneration,
  batchGenerate,
  getGenerations,
  getGenerationById,
  cancelGeneration,
} from '../controllers/generationController.js';

const router = express.Router();

router.post('/', protect, createGeneration);
router.post('/batch', protect, batchGenerate);
router.get('/', protect, getGenerations);
router.get('/:id', protect, getGenerationById);
router.post('/:id/cancel', protect, cancelGeneration);

export default router;
