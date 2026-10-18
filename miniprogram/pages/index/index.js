const api = require('../../utils/api');
const { round, mealLabel } = require('../../utils/format');
Page({
  data: { loading: true, today: null, error: '' },
  onShow() { this.load(); },
  async load() {
    this.setData({ loading: true, error: '' });
    try {
      await getApp().ensureAuth();
      const today = await api.getToday();
      today.consumedEnergy = round(today.consumed.energy_kcal);
      today.remainingEnergy = round(today.remaining.energy_kcal);
      today.gapsText = today.gaps && today.gaps.length ? `当前最需要补的是${today.gaps.slice(0, 2).join('和')}。` : '继续记录，系统会帮你看见饮食结构。';
      today.exerciseSummary = today.exercise_count ? `约 ${round(today.exercise_calories_burned)} 千卡运动消耗，估算值仅作日常记录参考。` : '还没有运动记录，可以说“今天打了60分钟网球”。';
      today.meals = (today.meals || []).map((meal) => ({ ...meal, mealLabel: mealLabel(meal.meal_type), energy: round(meal.nutrition.energy_kcal) }));
      today.exercises = (today.exercises || []).map((item) => ({ ...item, calories: round(item.calories_burned) }));
      getApp().globalData.today = today; this.setData({ today });
    }
    catch (error) { this.setData({ error: error.message || '今日数据加载失败' }); }
    finally { this.setData({ loading: false }); }
  },
  goAssistant() { wx.switchTab({ url: '/pages/assistant/assistant' }); },
  goRecord() { wx.switchTab({ url: '/pages/record/record' }); },
  goProfile() { wx.switchTab({ url: '/pages/profile/profile' }); },
});
