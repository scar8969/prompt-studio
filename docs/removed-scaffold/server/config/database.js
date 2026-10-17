import mongoose from 'mongoose';
import { createClient } from 'redis';

let redisClient = null;

export const connectDatabase = async () => {
  try {
    // Remove deprecated options - they're not needed in Mongoose 6+
    const conn = await mongoose.connect(process.env.MONGODB_URI);
    console.log(`✅ MongoDB Connected: ${conn.connection.host}`);
  } catch (error) {
    console.error(`❌ MongoDB Error: ${error.message}`);
    process.exit(1);
  }
};

export const connectRedis = async () => {
  // Redis is optional caching: skip unless explicitly enabled so dev machines
  // without a Redis server don't get connection-error spam.
  if (process.env.REDIS_ENABLED !== 'true') {
    return null;
  }
  try {
    redisClient = createClient({
      url: process.env.REDIS_URL || 'redis://localhost:6379',
      socket: {
        reconnectStrategy: false, // Don't spam reconnect attempts
      },
    });

    redisClient.on('error', (err) => {
      // Silence error spam - just log once
      if (!redisClient?.isReady) {
        console.warn('⚠️  Redis unavailable - caching disabled');
        redisClient = null;
      }
    });

    redisClient.on('connect', () => console.log('✅ Redis Connected'));

    await redisClient.connect();
    return redisClient;
  } catch (error) {
    console.warn(`⚠️  Redis connection failed - continuing without cache`);
    redisClient = null;
    return null;
  }
};

export const getRedisClient = () => redisClient;
