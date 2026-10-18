const api = require('../../utils/api');
Page({
  data: { form: { age: '', height_cm: '', current_weight_kg: '', exercise_habits: '' }, sexes: ['女性', '男性'], sexValues: ['female', 'male'], sexIndex: 0, activities: ['轻度活动', '中度活动', '高活动量'], activityValues: ['light', 'moderate', 'high'], activityIndex: 1, goals: ['改善饮食', '维持体重', '减脂', '增肌'], goalValues: ['structure', 'maintain', 'fat_loss', 'muscle_gain'], goalIndex: 0, saving: false, message: '', success: false },
  onShow() { this.load(); },
  async load() { try { await getApp().ensureAuth(); const profile = await api.getProfile(); this.setData({ form: { ...this.data.form, ...profile, exercise_habits: (profile.exercise_habits || []).join('、') }, sexIndex: Math.max(0, this.data.sexValues.indexOf(profile.sex)), activityIndex: Math.max(0, this.data.activityValues.indexOf(profile.activity_level)), goalIndex: Math.max(0, this.data.goalValues.indexOf(profile.primary_goal)) }); } catch (error) { this.setData({ message: error.message || '档案加载失败', success: false }); } },
  onField(event) { const field = event.currentTarget.dataset.field; const value = event.detail.value; this.setData({ [`form.${field}`]: field === 'exercise_habits' ? value : Number(value) || '' }); },
  onGoal(event) { this.setData({ goalIndex: Number(event.detail.value) }); },
  onSex(event) { this.setData({ sexIndex: Number(event.detail.value) }); },
  onActivity(event) { this.setData({ activityIndex: Number(event.detail.value) }); },
  async saveProfile() {
    const form = this.data.form; if (!form.age || !form.height_cm || !form.current_weight_kg) { this.setData({ message: '请先填写年龄、身高和体重。', success: false }); return; }
    this.setData({ saving: true, message: '' });
    try { await api.updateProfile({ age: Number(form.age), height_cm: Number(form.height_cm), current_weight_kg: Number(form.current_weight_kg), sex: this.data.sexValues[this.data.sexIndex], activity_level: this.data.activityValues[this.data.activityIndex], primary_goal: this.data.goalValues[this.data.goalIndex], goal_pace: form.goal_pace || 'standard', exercise_habits: String(form.exercise_habits || '').split(/[、,，]/).map((item) => item.trim()).filter(Boolean), safety_profile_reviewed: true }); const proposal = await api.recalculateGoal(); await api.confirmGoal(proposal.id); this.setData({ message: '档案已保存，每日目标已更新。', success: true }); } catch (error) { this.setData({ message: error.message || '保存失败，请检查信息后重试', success: false }); } finally { this.setData({ saving: false }); }
  },
  async exportData() { try { const data = await api.exportAccount(); wx.setClipboardData({ data: JSON.stringify(data, null, 2), success: () => wx.showToast({ title: '数据已复制', icon: 'success' }) }); } catch (error) { wx.showToast({ title: error.message || '导出失败', icon: 'none' }); } },
  openPrivacy() { wx.navigateTo({ url: '/pages/privacy/privacy' }); },
  deleteData() { wx.showModal({ title: '确认删除', content: '删除后账户、饮食、运动和 AI 记录将无法恢复。', confirmColor: '#a44934', success: async (result) => { if (!result.confirm) return; try { await api.deleteAccount(); wx.removeStorageSync('diet_access_token'); wx.reLaunch({ url: '/pages/index/index' }); } catch (error) { wx.showToast({ title: error.message || '删除失败', icon: 'none' }); } } }); },
});
