import express from 'express';
import { protect } from '../middleware/auth.js';
import {
  getAssets,
  getAssetById,
  updateAsset,
  deleteAsset,
  incrementExport,
  getTopAssets,
} from '../controllers/assetController.js';

const router = express.Router();

router.get('/', protect, getAssets);
router.get('/top', protect, getTopAssets);
router.get('/:id', protect, getAssetById);
router.put('/:id', protect, updateAsset);
router.delete('/:id', protect, deleteAsset);
router.post('/:id/export', protect, incrementExport);

export default router;
