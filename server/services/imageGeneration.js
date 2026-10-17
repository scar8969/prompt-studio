import axios from 'axios';
import Generation from '../models/Generation.js';
import Asset from '../models/Asset.js';

class ImageGenerationService {
  constructor() {
    this.replicateToken = process.env.REPLICATE_API_TOKEN;
    this.stabilityKey = process.env.STABILITY_API_KEY;
  }

  async generateWithFlux(prompt, parameters, userId, projectId) {
    const generation = await Generation.create({
      jobId: `flux-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      prompt,
      model: 'flux-1.1-pro',
      parameters,
      status: 'processing',
      user: userId,
      project: projectId,
    });

    try {
      // Simulate API call to Replicate Flux API
      const response = await axios.post(
        'https://api.replicate.com/v1/predictions',
        {
          version: 'black-forest-labs/flux-1.1-pro',
          input: {
            prompt,
            width: parameters.width || 1024,
            height: parameters.height || 1024,
            num_inference_steps: parameters.steps || 30,
            guidance_scale: parameters.cfgScale || 3.5,
            seed: parameters.seed || Math.floor(Math.random() * 1000000),
          },
        },
        {
          headers: {
            Authorization: `Token ${this.replicateToken}`,
            'Content-Type': 'application/json',
          },
        }
      );

      generation.progress = 50;
      await generation.save();

      // Poll for completion (simplified)
      const result = await this.pollPrediction(response.data.id);

      if (result.status === 'succeeded') {
        // Create asset
        const asset = await Asset.create({
          filename: `flux-${Date.now()}.png`,
          originalName: `${prompt.substring(0, 30)}.png`,
          fileType: 'image',
          mimeType: 'image/png',
          size: 4200000,
          width: parameters.width,
          height: parameters.height,
          aspectRatio: `${parameters.width}:${parameters.height}`,
          url: result.output[0],
          metadata: {
            model: 'flux-1.1-pro',
            prompt,
            seed: parameters.seed,
            parameters,
          },
          quality: Math.random() * 2 + 8,
          generation: generation._id,
          project: projectId,
          user: userId,
          status: 'ready',
        });

        generation.status = 'completed';
        generation.progress = 100;
        generation.outputUrls = [result.output[0]];
        generation.assetIds = [asset._id];
        generation.computeCost = 4.5;
        generation.duration = 5100;
        await generation.save();

        return { generation, asset };
      } else {
        throw new Error('Generation failed');
      }
    } catch (error) {
      generation.status = 'failed';
      generation.error = error.message;
      await generation.save();
      throw error;
    }
  }

  async generateWithSDXL(prompt, parameters, userId, projectId) {
    const generation = await Generation.create({
      jobId: `sdxl-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      prompt,
      model: 'sdxl-turbo',
      parameters,
      status: 'processing',
      user: userId,
      project: projectId,
    });

    try {
      // Mock SDXL generation
      await new Promise((resolve) => setTimeout(resolve, 3000));

      const mockUrl = `https://replicate.delivery/pbxt/mock-${Date.now()}.png`;

      const asset = await Asset.create({
        filename: `sdxl-${Date.now()}.png`,
        originalName: `${prompt.substring(0, 30)}.png`,
        fileType: 'image',
        mimeType: 'image/png',
        size: 2100000,
        width: parameters.width,
        height: parameters.height,
        aspectRatio: `${parameters.width}:${parameters.height}`,
        url: mockUrl,
        metadata: {
          model: 'sdxl-turbo',
          prompt,
          seed: parameters.seed,
          parameters,
        },
        quality: Math.random() * 2 + 8,
        generation: generation._id,
        project: projectId,
        user: userId,
        status: 'ready',
      });

      generation.status = 'completed';
      generation.progress = 100;
      generation.outputUrls = [mockUrl];
      generation.assetIds = [asset._id];
      generation.computeCost = 2.0;
      generation.duration = 4200;
      await generation.save();

      return { generation, asset };
    } catch (error) {
      generation.status = 'failed';
      generation.error = error.message;
      await generation.save();
      throw error;
    }
  }

  async pollPrediction(predictionId) {
    // Simplified polling logic
    await new Promise((resolve) => setTimeout(resolve, 3000));
    return {
      status: 'succeeded',
      output: [`https://replicate.delivery/pbxt/mock-${Date.now()}.png`],
    };
  }

  async batchGenerate(jobs, userId, projectId) {
    const results = [];
    for (const job of jobs) {
      try {
        const result =
          job.model === 'flux-1.1-pro'
            ? await this.generateWithFlux(job.prompt, job.parameters, userId, projectId)
            : await this.generateWithSDXL(job.prompt, job.parameters, userId, projectId);
        results.push(result);
      } catch (error) {
        results.push({ error: error.message, job });
      }
    }
    return results;
  }
}

export default new ImageGenerationService();
