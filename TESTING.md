# Testing Guide for Aetheria AI Creative Studio

## Overview

This document provides comprehensive testing instructions to verify the full functionality of the application.

## Prerequisites

- Node.js 18+ installed
- MongoDB running locally or Atlas URI configured
- All dependencies installed (`npm install`)

## Testing Checklist

### 1. Backend API Tests

Run the automated test suite:

```bash
npm test
```

Expected output:
- ✅ All authentication tests pass
- ✅ All project CRUD tests pass
- ✅ Generation API tests pass
- ✅ Analytics endpoints respond correctly

### 2. Manual Backend Testing

#### A. Health Check
```bash
curl http://localhost:5000/api/health
```
Expected: `{"status":"OK","timestamp":"..."}`

#### B. User Registration
```bash
curl -X POST http://localhost:5000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Test User","email":"test@example.com","password":"password123"}'
```
Expected: Returns user object and JWT token

#### C. Login
```bash
curl -X POST http://localhost:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password123"}'
```
Expected: Returns user and token

### 3. Frontend Tests

#### A. Login Flow
1. Navigate to http://localhost:3000
2. Should redirect to `/login`
3. Click "Don't have an account? Sign up"
4. Fill in registration form
5. Submit and verify redirect to main app
6. Verify user name appears in sidebar

#### B. Project Creation
1. Click "New Project" in sidebar
2. Should open project creation modal (or redirect)
3. Enter project details
4. Submit and verify project appears in project list

#### C. Chat Studio
1. Navigate to Chat Studio (should be default)
2. Enter prompt: "A beautiful mountain landscape"
3. Click "Send & Plan"
4. Verify:
   - Loading indicator appears
   - User message shows in chat
   - AI response appears
   - Generation status updates

#### D. Gallery View
1. Navigate to Gallery from sidebar
2. Verify:
   - Assets display in masonry layout
   - Clicking asset shows inspector panel
   - Quality scores display
   - Filters work (sort by date, quality, etc.)

#### E. Analytics Dashboard
1. Navigate to Analytics
2. Verify:
   - Quota usage displays with circular progress
   - KPI tiles show correct data
   - Charts render properly
   - Period selector works (7d, 30d, 90d)

### 4. Integration Testing

#### Scenario 1: Complete Generation Workflow

1. **Create Project**
   - Click "New Project"
   - Name: "Test Campaign"
   - Description: "Testing workflow"
   - Submit

2. **Generate Images**
   - Go to Chat Studio
   - Enter: "Minimalist product photography"
   - Submit and wait for completion
   - Verify asset appears in response

3. **View in Gallery**
   - Navigate to Gallery
   - Verify new asset appears
   - Click asset to open inspector
   - Check metadata displays correctly

4. **Check Analytics**
   - Navigate to Analytics
   - Verify generation count increased
   - Check compute usage updated
   - Verify quota decreased

#### Scenario 2: Batch Generation

1. Use batch generation endpoint:
```bash
curl -X POST http://localhost:5000/api/generations/batch \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "projectId": "PROJECT_ID",
    "jobs": [
      {
        "prompt": "Image 1",
        "model": "sdxl-turbo",
        "parameters": {"width": 1024, "height": 1024}
      },
      {
        "prompt": "Image 2",
        "model": "sdxl-turbo",
        "parameters": {"width": 1024, "height": 1024}
      }
    ]
  }'
```

2. Verify all jobs complete
3. Check Gallery for new assets

### 5. Error Handling Tests

#### A. Invalid Login
1. Try logging in with wrong password
2. Verify error message displays

#### B. Quota Exceeded
1. Manually set user quota to 0 in MongoDB:
```javascript
db.users.updateOne(
  {email: "test@example.com"},
  {$set: {"quota.computeUnits.used": 10000}}
)
```
2. Try generating image
3. Verify "Quota exceeded" error

#### C. Network Errors
1. Stop backend server
2. Try any frontend action
3. Verify graceful error handling

### 6. Performance Tests

#### Load Test Generation API
```bash
# Install Apache Bench
# Run 100 requests with 10 concurrent
ab -n 100 -c 10 -H "Authorization: Bearer TOKEN" \
  http://localhost:5000/api/generations
```

Expected: 
- All requests succeed
- Average response time < 1000ms

### 7. Security Tests

#### A. Unauthorized Access
```bash
# Try accessing protected endpoint without token
curl http://localhost:5000/api/projects
```
Expected: 401 Unauthorized

#### B. Token Validation
```bash
# Try with invalid token
curl -H "Authorization: Bearer invalid_token" \
  http://localhost:5000/api/projects
```
Expected: 401 Unauthorized

#### C. SQL Injection Prevention
Try entering in prompts:
- `'; DROP TABLE users; --`
- `<script>alert('XSS')</script>`
Expected: Treated as normal text, no code execution

### 8. Browser Compatibility

Test in:
- ✅ Chrome (latest)
- ✅ Firefox (latest)
- ✅ Safari (latest)
- ✅ Edge (latest)

Verify:
- UI renders correctly
- All interactions work
- No console errors

### 9. Mobile Responsiveness

Test on:
- Mobile viewport (375px width)
- Tablet viewport (768px width)
- Desktop viewport (1920px width)

Verify:
- Layout adapts appropriately
- Touch interactions work
- No horizontal scrolling

### 10. Database Tests

#### A. Data Persistence
1. Create project
2. Restart server
3. Verify project still exists

#### B. Relationships
1. Create project with generations
2. Query project
3. Verify associated generations populate

#### C. Indexes
```bash
# Connect to MongoDB
mongosh aetheria-studio

# Check indexes
db.generations.getIndexes()
db.assets.getIndexes()
db.projects.getIndexes()
```

Expected: Indexes on user, project, status fields

## Continuous Testing

### Watch Mode
```bash
# Run tests in watch mode during development
npm test -- --watch
```

### Pre-commit Hook
Add to `.git/hooks/pre-commit`:
```bash
#!/bin/sh
npm test
```

## Reporting Issues

When reporting bugs, include:
1. Steps to reproduce
2. Expected behavior
3. Actual behavior
4. Browser/OS information
5. Console errors
6. Network request details

## Test Coverage Goals

- Backend: > 80% coverage
- Frontend: > 70% coverage
- E2E: All critical paths covered

Run coverage report:
```bash
npm run test:coverage
```

## Success Criteria

All tests pass when:
- ✅ Backend API responds correctly
- ✅ Frontend loads without errors
- ✅ User can register/login
- ✅ Projects can be created
- ✅ Generations complete successfully
- ✅ Assets appear in gallery
- ✅ Analytics display correctly
- ✅ No console errors
- ✅ No memory leaks
- ✅ Performance within acceptable limits

🎉 Application is production-ready!
