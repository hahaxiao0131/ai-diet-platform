const { request, ensureAuth } = require('./request');

module.exports = {
  ensureAuth,
  getProfile: () => request('/profiles/me'),
  updateProfile: (data) => request('/profiles/me', { method: 'PUT', data }),
  recalculateGoal: () => request('/goals/recalculate', { method: 'POST' }),
  confirmGoal: (id) => request(`/goals/${id}/confirm`, { method: 'POST' }),
  getGoal: () => request('/goals/current'),
  getToday: () => request('/today'),
  getExercises: () => request('/exercises'),
  createExercise: (data) => request('/exercises', { method: 'POST', data }),
  searchFoods: (query) => request(`/foods/search?q=${encodeURIComponent(query)}`),
  createManualMeal: (data) => request('/meals/manual', { method: 'POST', data }),
  chat: (message, replyToTraceId) => request('/ai/chat', { method: 'POST', data: { message, reply_to_trace_id: replyToTraceId || null } }),
  confirmAiAction: (id, revision) => request(`/ai/actions/${id}/confirm`, { method: 'POST', data: { expected_revision: revision } }),
  cancelAiAction: (id, revision) => request(`/ai/actions/${id}/cancel`, { method: 'POST', data: { expected_revision: revision } }),
  exportAccount: () => request('/account/export'),
  deleteAccount: () => request('/account', { method: 'DELETE', data: { confirmation: '永久删除' } }),
};
