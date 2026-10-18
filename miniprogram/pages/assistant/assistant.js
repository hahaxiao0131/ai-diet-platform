const api = require('../../utils/api');
Page({
  data: { input: '', sending: false, messages: [] },
  onInput(event) { this.setData({ input: event.detail.value }); },
  async send() {
    const message = (this.data.input || '').trim(); if (!message || this.data.sending) return;
    this.setData({ input: '', sending: true, messages: this.data.messages.concat([{ id: `u-${Date.now()}`, role: 'user', text: message }]) });
    try {
      await getApp().ensureAuth();
      const profile = await api.getProfile();
      const goal = await api.getGoal();
      if (!profile.onboarding_completed || !goal) {
        this.setData({ messages: this.data.messages.concat([{ id: `setup-${Date.now()}`, role: 'assistant', text: '请先在“我的”中完善基础信息并确认每日目标，之后我才能准确估算营养和运动消耗。' }]) });
        wx.showModal({ title: '需要先完善档案', content: '请先填写年龄、身高、体重和目标。', confirmText: '去完善', success: (result) => { if (result.confirm) wx.switchTab({ url: '/pages/profile/profile' }); } });
        return;
      }
      const response = await api.chat(message, null);
      response.basisText = (response.basis || []).join('；');
      this.setData({ messages: this.data.messages.concat([{ id: `a-${Date.now()}`, role: 'assistant', text: response.message, response }]) });
    } catch (error) { this.setData({ messages: this.data.messages.concat([{ id: `e-${Date.now()}`, role: 'assistant', text: error.message || '暂时无法处理，请稍后重试' }]) }); }
    finally { this.setData({ sending: false }); }
  },
  async confirmAction(event) {
    try {
      const result = await api.confirmAiAction(event.currentTarget.dataset.id, Number(event.currentTarget.dataset.revision));
      const messages = this.data.messages.map((item) => item.response && item.response.action && item.response.action.id === result.action.id ? { ...item, response: { ...item.response, action: result.action } } : item);
      this.setData({ messages: messages.concat([{ id: `ok-${Date.now()}`, role: 'assistant', text: result.exercise ? '已计入今日运动。' : result.meal ? '已计入今日饮食。' : '已完成确认。' }]) });
    } catch (error) { wx.showToast({ title: error.message || '确认失败', icon: 'none' }); }
  },
  async cancelAction(event) {
    try {
      const action = await api.cancelAiAction(event.currentTarget.dataset.id, Number(event.currentTarget.dataset.revision));
      this.setData({ messages: this.data.messages.map((item) => item.response && item.response.action && item.response.action.id === action.id ? { ...item, response: { ...item.response, action } } : item) });
    } catch (error) { wx.showToast({ title: error.message || '取消失败', icon: 'none' }); }
  },
});
