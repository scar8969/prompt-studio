import React, { createContext, useState, useContext, useEffect } from 'react';
import api from '../services/api';

const ProjectContext = createContext();

export const useProject = () => {
  const context = useContext(ProjectContext);
  if (!context) {
    throw new Error('useProject must be used within ProjectProvider');
  }
  return context;
};

export const ProjectProvider = ({ children }) => {
  const [projects, setProjects] = useState([]);
  const [currentProject, setCurrentProject] = useState(null);
  const [loading, setLoading] = useState(false);

  const fetchProjects = async () => {
    setLoading(true);
    try {
      const response = await api.get('/projects');
      setProjects(response.data.projects);
      if (response.data.projects.length > 0 && !currentProject) {
        setCurrentProject(response.data.projects[0]);
      }
    } catch (error) {
      console.error('Error fetching projects:', error);
    } finally {
      setLoading(false);
    }
  };

  const createProject = async (projectData) => {
    try {
      const response = await api.post('/projects', projectData);
      setProjects([response.data, ...projects]);
      setCurrentProject(response.data);
      return { success: true, project: response.data };
    } catch (error) {
      return {
        success: false,
        error: error.response?.data?.error || 'Failed to create project'
      };
    }
  };

  const updateProject = async (id, updates) => {
    try {
      const response = await api.put(`/projects/${id}`, updates);
      setProjects(projects.map((p) => (p._id === id ? response.data : p)));
      if (currentProject?._id === id) {
        setCurrentProject(response.data);
      }
      return { success: true, project: response.data };
    } catch (error) {
      return {
        success: false,
        error: error.response?.data?.error || 'Failed to update project'
      };
    }
  };

  const deleteProject = async (id) => {
    try {
      await api.delete(`/projects/${id}`);
      setProjects(projects.filter((p) => p._id !== id));
      if (currentProject?._id === id) {
        setCurrentProject(projects[0] || null);
      }
      return { success: true };
    } catch (error) {
      return {
        success: false,
        error: error.response?.data?.error || 'Failed to delete project'
      };
    }
  };

  useEffect(() => {
    fetchProjects();
  }, []);

  const value = {
    projects,
    currentProject,
    setCurrentProject,
    loading,
    fetchProjects,
    createProject,
    updateProject,
    deleteProject,
  };

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
};
