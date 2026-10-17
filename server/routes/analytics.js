import express from 'express';
import { protect } from '../middleware/auth.js';
import { getAnalytics, getQuotaStatus } from '../controllers/analyticsController.js';

const router = express.Router();

router.get('/', protect, getAnalytics);
router.get('/quota', protect, getQuotaStatus);

export default router;
