const api = require('./utils/api');
const { cloudDevEnvId } = require('./utils/config');

App({
  globalData: { profile: null, today: null },
  onLaunch() {
    if (wx.cloud && cloudDevEnvId) {
      wx.cloud.init({ env: cloudDevEnvId, traceUser: true });
    }
    this.authReady = this.preparePrivacy().then(() => api.ensureAuth()).catch((error) => {
      this.authError = error;
      return null;
    });
  },
  preparePrivacy() {
    return new Promise((resolve, reject) => {
      if (!wx.getPrivacySetting) { resolve(); return; }
      wx.getPrivacySetting({
        success: (result) => {
          if (!result.needAuthorization || !wx.requirePrivacyAuthorize) { resolve(); return; }
          wx.requirePrivacyAuthorize({ success: resolve, fail: reject });
        },
        fail: resolve,
      });
    });
  },
  ensureAuth() {
    return this.authReady || api.ensureAuth();
  },
});
