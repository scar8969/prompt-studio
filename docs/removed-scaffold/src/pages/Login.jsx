import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';

const Login = () => {
  const [isLogin, setIsLogin] = useState(true);
  const [formData, setFormData] = useState({
    name: '',
    email: '',
    password: '',
  });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login, register } = useAuth();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    const result = isLogin
      ? await login(formData.email, formData.password)
      : await register(formData.name, formData.email, formData.password);

    if (!result.success) {
      setError(result.error);
    }
    setLoading(false);
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-md modal-enter">
        <div className="bg-surface-container-low border border-outline-variant rounded-xl p-8 shadow-xl">
          <div className="text-center mb-8">
            <div className="flex justify-center mb-4">
              <div className="w-16 h-16 rounded-lg bg-gradient-to-tr from-secondary-container to-primary-container flex items-center justify-center shadow-lg">
                <span className="material-symbols-outlined text-primary text-3xl">
                  auto_awesome
                </span>
              </div>
            </div>
            <h1 className="font-headline-md text-headline-md font-semibold text-on-surface mb-2">
              Latent Studio OS
            </h1>
            <p className="font-code-sm text-code-sm text-outline">
              v2.4 Pro Creative Engine
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {!isLogin && (
              <div>
                <label className="font-param-label text-param-label text-on-surface-variant block mb-1">
                  Name
                </label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  className="w-full h-10 bg-surface-container-lowest border border-outline-variant focus:border-primary focus:ring-1 focus:ring-primary rounded-lg px-3 font-body-base text-body-base text-on-surface outline-none transition-colors-150"
                  required={!isLogin}
                />
              </div>
            )}

            <div>
              <label className="font-param-label text-param-label text-on-surface-variant block mb-1">
                Email
              </label>
              <input
                type="email"
                value={formData.email}
                onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                className="w-full h-10 bg-surface-container-lowest border border-outline-variant focus:border-primary focus:ring-1 focus:ring-primary rounded-lg px-3 font-body-base text-body-base text-on-surface outline-none transition-colors-150"
                required
              />
            </div>

            <div>
              <label className="font-param-label text-param-label text-on-surface-variant block mb-1">
                Password
              </label>
              <input
                type="password"
                value={formData.password}
                onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                className="w-full h-10 bg-surface-container-lowest border border-outline-variant focus:border-primary focus:ring-1 focus:ring-primary rounded-lg px-3 font-body-base text-body-base text-on-surface outline-none transition-colors-150"
                required
              />
            </div>

            {error && (
              <div className="bg-error-container border border-error rounded-lg p-3 font-body-sm text-body-sm text-on-error-container toast-enter">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full h-10 rounded-lg bg-primary text-on-primary font-title-sm text-body-sm font-semibold flex items-center justify-center gap-2 shadow-md shadow-primary/30 transition-transform-200 active:scale-[0.97] hover:brightness-110 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? (
                <div className="w-5 h-5 border-2 border-on-primary border-t-transparent rounded-full spinner-fast" />
              ) : (
                <>
                  <span className="material-symbols-outlined text-base">bolt</span>
                  {isLogin ? 'Sign In' : 'Create Account'}
                </>
              )}
            </button>
          </form>

          <div className="mt-6 text-center">
            <button
              onClick={() => {
                setIsLogin(!isLogin);
                setError('');
              }}
              className="font-body-sm text-body-sm text-primary hover:underline transition-colors-150"
            >
              {isLogin
                ? "Don't have an account? Sign up"
                : 'Already have an account? Sign in'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Login;
