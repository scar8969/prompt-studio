import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import request from 'supertest';
import app from '../server/index.js';
import mongoose from 'mongoose';
import User from '../server/models/User.js';
import Project from '../server/models/Project.js';

let authToken;
let userId;
let projectId;

beforeAll(async () => {
  // Connect to test database
  await mongoose.connect(process.env.MONGODB_URI || 'mongodb://localhost:27017/aetheria-test');

  // Clean up
  await User.deleteMany({});
  await Project.deleteMany({});

  // Register test user
  const registerRes = await request(app)
    .post('/api/auth/register')
    .send({
      name: 'Test User',
      email: 'test@example.com',
      password: 'password123',
    });

  authToken = registerRes.body.token;
  userId = registerRes.body.user.id;
});

afterAll(async () => {
  await mongoose.connection.close();
});

describe('Authentication API', () => {
  it('should register a new user', async () => {
    const res = await request(app)
      .post('/api/auth/register')
      .send({
        name: 'New User',
        email: 'newuser@example.com',
        password: 'password123',
      });

    expect(res.status).toBe(201);
    expect(res.body).toHaveProperty('token');
    expect(res.body.user).toHaveProperty('email', 'newuser@example.com');
  });

  it('should login existing user', async () => {
    const res = await request(app)
      .post('/api/auth/login')
      .send({
        email: 'test@example.com',
        password: 'password123',
      });

    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('token');
  });

  it('should fail with invalid credentials', async () => {
    const res = await request(app)
      .post('/api/auth/login')
      .send({
        email: 'test@example.com',
        password: 'wrongpassword',
      });

    expect(res.status).toBe(401);
  });

  it('should get user profile', async () => {
    const res = await request(app)
      .get('/api/auth/me')
      .set('Authorization', `Bearer ${authToken}`);

    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('email', 'test@example.com');
  });
});

describe('Project API', () => {
  it('should create a new project', async () => {
    const res = await request(app)
      .post('/api/projects')
      .set('Authorization', `Bearer ${authToken}`)
      .send({
        title: 'Test Project',
        description: 'A test project',
        tags: ['test', 'demo'],
      });

    expect(res.status).toBe(201);
    expect(res.body).toHaveProperty('title', 'Test Project');
    projectId = res.body._id;
  });

  it('should get all projects', async () => {
    const res = await request(app)
      .get('/api/projects')
      .set('Authorization', `Bearer ${authToken}`);

    expect(res.status).toBe(200);
    expect(res.body.projects).toBeInstanceOf(Array);
    expect(res.body.projects.length).toBeGreaterThan(0);
  });

  it('should get project by id', async () => {
    const res = await request(app)
      .get(`/api/projects/${projectId}`)
      .set('Authorization', `Bearer ${authToken}`);

    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('_id', projectId);
  });

  it('should update project', async () => {
    const res = await request(app)
      .put(`/api/projects/${projectId}`)
      .set('Authorization', `Bearer ${authToken}`)
      .send({
        title: 'Updated Test Project',
      });

    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('title', 'Updated Test Project');
  });
});

describe('Generation API', () => {
  it('should create a generation', async () => {
    const res = await request(app)
      .post('/api/generations')
      .set('Authorization', `Bearer ${authToken}`)
      .send({
        prompt: 'A beautiful landscape',
        model: 'sdxl-turbo',
        parameters: {
          width: 1024,
          height: 1024,
          steps: 20,
        },
        projectId,
      });

    expect(res.status).toBe(201);
    expect(res.body).toHaveProperty('generation');
    expect(res.body.generation).toHaveProperty('status');
  });

  it('should get generations', async () => {
    const res = await request(app)
      .get('/api/generations')
      .set('Authorization', `Bearer ${authToken}`);

    expect(res.status).toBe(200);
    expect(res.body.generations).toBeInstanceOf(Array);
  });
});

describe('Analytics API', () => {
  it('should get analytics data', async () => {
    const res = await request(app)
      .get('/api/analytics?period=7d')
      .set('Authorization', `Bearer ${authToken}`);

    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('overview');
    expect(res.body).toHaveProperty('quota');
  });

  it('should get quota status', async () => {
    const res = await request(app)
      .get('/api/analytics/quota')
      .set('Authorization', `Bearer ${authToken}`);

    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('computeUnits');
  });
});
