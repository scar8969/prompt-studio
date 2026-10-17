## Quick Start Guide

### 🚀 Getting Started in 3 Steps

#### Step 1: Install Dependencies
```bash
npm install
```

#### Step 2: Start MongoDB
Make sure MongoDB is running on your system:
```bash
# Windows
mongod

# macOS/Linux
sudo service mongod start

# Or use MongoDB Atlas (cloud) by updating MONGODB_URI in .env
```

#### Step 3: Run the Application
```bash
npm run dev
```

The application will start:
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:5000
- **API Health Check**: http://localhost:5000/api/health

### 📝 First Time Setup

1. **Register an Account**
   - Navigate to http://localhost:3000
   - Click "Sign up" and create your account
   - You'll get 10,000 compute units to start

2. **Create a Project**
   - Once logged in, the system will prompt you to create a project
   - Or manually create one from the sidebar

3. **Start Generating**
   - Go to Chat Studio
   - Enter a prompt like: "A minimalist black bottle on obsidian stone with morning mist"
   - Click "Send & Plan" or press Enter

### 🎨 Example Prompts

Try these prompts for best results:

**Product Photography:**
```
Minimalist luxury cold brew bottle on obsidian pedestal, morning mist, 
cool rim light, editorial product photography, 8k resolution
```

**Macro Details:**
```
Extreme close-up of water condensation droplets on dark glass, 
warm bronze rim light, macro photography, shallow depth of field
```

**Architectural Scenes:**
```
Concrete brutalist interior, diagonal sunlight through louvers, 
bottle product placement, architectural photography, Nordic aesthetic
```

### 🔧 Configuration

#### Using Real AI Models

To connect real AI generation services:

1. **Get API Keys:**
   - Replicate (Flux): https://replicate.com/account/api-tokens
   - Anthropic (Claude): https://console.anthropic.com/
   - Stability AI: https://platform.stability.ai/

2. **Update `.env` file:**
```env
REPLICATE_API_TOKEN=r8_your_actual_token_here
ANTHROPIC_API_KEY=sk-ant-your_actual_key_here
STABILITY_API_KEY=sk-your_actual_key_here
```

3. **Restart the server:**
```bash
npm run server
```

### 📊 Features Overview

| Feature | Description | Status |
|---------|-------------|--------|
| Chat Studio | AI-powered image generation interface | ✅ Ready |
| Asset Library | Masonry gallery with filtering | ✅ Ready |
| Analytics | Usage tracking & quota monitoring | ✅ Ready |
| Project Management | Organize generations into projects | ✅ Ready |
| Real-time Updates | WebSocket generation progress | ✅ Ready |
| User Auth | JWT-based authentication | ✅ Ready |
| Batch Generation | Multiple images at once | ✅ Ready |

### 🧪 Running Tests

```bash
# Run all tests
npm test

# Run with coverage
npm run test:coverage
```

### 🐛 Troubleshooting

**MongoDB Connection Error:**
```bash
# Make sure MongoDB is installed and running
mongod --version

# Or use MongoDB Atlas cloud database
# Update MONGODB_URI in .env to your Atlas connection string
```

**Port Already in Use:**
```bash
# Change ports in .env:
PORT=5001  # Backend
# And in vite.config.js for frontend
```

**API Returns 401 Unauthorized:**
- Clear localStorage in browser DevTools
- Log in again to get a fresh token

### 📱 Default Credentials (After Registration)

After you register, you'll have:
- **Role**: User
- **Compute Quota**: 10,000 CU
- **Models**: Flux 1.1 Pro, SDXL Turbo
- **Features**: All features unlocked

### 🎯 Next Steps

1. **Explore the Interface**: Navigate through Chat Studio, Gallery, and Analytics
2. **Generate Images**: Try different prompts and models
3. **Manage Assets**: Tag and organize your generations
4. **Monitor Usage**: Check your quota and performance metrics
5. **Customize**: Update model presets and project settings

### 📚 Learn More

- [Full Documentation](README.md)
- [API Reference](#api-endpoints)
- [Architecture Guide](#project-structure)

### 💡 Tips

- Use **Enter** to send prompts, **Shift+Enter** for new lines
- Quality scores are calculated automatically (8-10 range)
- Export counts track how many times assets are downloaded
- Projects help organize related generations
- Redis is optional but improves performance

Enjoy creating with Aetheria AI Creative Studio! 🎨✨
