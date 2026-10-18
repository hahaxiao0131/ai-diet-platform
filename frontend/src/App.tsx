import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Activity,
  BarChart3,
  BadgeCheck,
  Bot,
  Brain,
  Check,
  ChevronDown,
  Clock3,
  Download,
  FileText,
  Flame,
  ExternalLink,
  Home,
  Info,
  Leaf,
  ListPlus,
  MessageCircle,
  Mic,
  Pencil,
  Plus,
  RefreshCw,
  Scale,
  Search,
  Send,
  ScanBarcode,
  ShieldAlert,
  Sparkles,
  Square,
  Trash2,
  ThumbsDown,
  ThumbsUp,
  UserRound,
  Utensils,
  X,
} from "lucide-react";
import { AgentMemory, AIAction, AIChatResponse, AIServiceStatus, api, ApiError, AuthSession, BodyFeedbackInput, DietAssessment, Food, GoalProposal, Meal, MealDraft, MealDraftItem, MealPlan, MealType, Nutrition, Profile, SevenDayTrend, TodaySummary, setAccessToken } from "./api";
import { appendMessageReconcilingAction, updateActionEverywhere } from "./actionReconciliation";
import { historyTurnsToMessages, mergeRestoredMessages, type RestorableAssistantMessage } from "./historyRestoration";
import { presentSafetyRisk, primaryRecordedWarning } from "./riskPresentation.ts";
import { accountExportFilename, ACCOUNT_DELETE_CONFIRMATION, isAccountDeleteConfirmation, serializeAccountExport } from "./accountDataControl.ts";

type Tab = "today" | "record" | "trends" | "assistant" | "profile";
type PendingActionEdit = {
  actionId: string;
  mealType: MealType;
  items: { foodId: string; weightG: number; consumedRatio: number }[];
};

const mealTypeLabels: Record<MealType, string> = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐", snack: "加餐" };
const goalLabels: Record<NonNullable<Profile["primary_goal"]>, string> = { fat_loss: "减脂", muscle_gain: "增肌", maintain: "维持体重", structure: "改善饮食" };
const activityLabels: Record<NonNullable<Profile["activity_level"]>, string> = { sedentary: "久坐少动", light: "轻度活动", moderate: "中度活动", high: "高活动量" };
const symptomOptions = ["无不适", "腹胀", "腹泻", "犯困", "反酸", "偏头痛", "恶心"];

function currentMealType(): MealType {
  const hour = new Date().getHours();
  if (hour < 10) return "breakfast";
  if (hour < 15) return "lunch";
  if (hour < 21) return "dinner";
  return "snack";
}

const emptyNutrition: Nutrition = {
  energy_kcal: 0,
  protein_g: 0,
  fat_g: 0,
  carbs_g: 0,
  fiber_g: 0,
  sodium_mg: 0,
  sugars_g: null,
  added_sugar_g: 0,
  vegetable_g: 0,
  fruit_g: 0,
};

function App() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [session, setSession] = useState<AuthSession | null>(null);
  const [goal, setGoal] = useState<GoalProposal | null>(null);
  const [today, setToday] = useState<TodaySummary | null>(null);
  const [tab, setTab] = useState<Tab>("today");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<MealDraft | null>(null);
  const [plans, setPlans] = useState<MealPlan[]>([]);
  const [showPlans, setShowPlans] = useState(false);
  const [showManualRecord, setShowManualRecord] = useState(false);
  const [manualInitialItems, setManualInitialItems] = useState<ManualMealSelection[]>([]);
  const [showNutritionInfo, setShowNutritionInfo] = useState(false);
  const [showProfileEdit, setShowProfileEdit] = useState(false);
  const [feedbackMeal, setFeedbackMeal] = useState<Meal | null>(null);
  const [assessment, setAssessment] = useState<DietAssessment | null>(null);

  async function refresh() {
    try {
      const [nextProfile, nextGoal] = await Promise.all([api.getProfile(), api.getGoal()]);
      setProfile(nextProfile);
      setGoal(nextGoal);
      if (nextGoal) setToday(await api.getToday());
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    api.restoreSession()
      .then((restored) => {
        setSession(restored);
        return refresh();
      })
      .catch(() => {
        setAccessToken("");
        setLoading(false);
      });
  }, []);

  async function completeLogin(nextSession: AuthSession) {
    if (!nextSession.access_token) throw new Error("登录响应缺少会话令牌");
    setAccessToken(nextSession.access_token);
    setSession(nextSession);
    setLoading(true);
    await refresh();
  }

  async function logout() {
    try {
      await api.logout();
    } finally {
      setAccessToken("");
      setSession(null);
      setProfile(null);
      setGoal(null);
      setToday(null);
      setTab("today");
    }
  }

  async function deleteAccount() {
    await api.deleteAccount(ACCOUNT_DELETE_CONFIRMATION);
    setAccessToken("");
    setSession(null);
    setProfile(null);
    setGoal(null);
    setToday(null);
    setTab("today");
  }

  async function finishOnboarding(payload: Partial<Profile>) {
    setLoading(true);
    try {
      await api.updateProfile(payload);
      const nextGoal = await api.recalculateGoal();
      await api.confirmGoal(nextGoal.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "建档失败");
      setLoading(false);
    }
  }

  async function openExampleDraft() {
    try {
      setDraft(await api.createExampleDraft(currentMealType()));
      setTab("record");
    } catch (err) {
      setError(err instanceof Error ? err.message : "示例草稿创建失败");
    }
  }

  async function confirmDraft(items: MealDraftItem[]) {
    if (!draft) return;
    const meal = await api.confirmDraft(draft, items);
    setDraft(null);
    setToday(await api.getToday());
    const recordedRisk = primaryRecordedWarning(meal.risks);
    setNotice(recordedRisk
      ? presentSafetyRisk(recordedRisk, "recorded")
      : "餐次已保存，今日营养数据已更新");
    setTab("today");
  }

  async function addManualMeal(items: ManualMealSelection[], mealType: MealType, bodyFeedback?: BodyFeedbackInput | null) {
    const meal = await api.createManualMeal({
      meal_type: mealType,
      items: items.map((item) => ({
        food_id: item.food.id,
        weight_g: item.weight_g,
        consumed_ratio: 1,
      })),
      body_feedback: bodyFeedback,
    });
    setShowManualRecord(false);
    setToday(await api.getToday());
    const recordedRisk = primaryRecordedWarning(meal.risks);
    setNotice(recordedRisk ? presentSafetyRisk(recordedRisk, "recorded") : `${mealTypeLabels[mealType]}已保存`);
    setTab("today");
  }

  async function saveBodyFeedback(meal: Meal, feedback: BodyFeedbackInput) {
    await api.saveMealFeedback(meal.id, feedback);
    setToday(await api.getToday());
    setFeedbackMeal(null);
    setAssessment(await api.assessDiet({ meal_id: meal.id }));
    setNotice("体感已保存，并更新了个人饮食反应记忆");
  }

  function openManual(items: ManualMealSelection[] = []) {
    setManualInitialItems(items);
    setShowManualRecord(true);
  }

  async function openQuickRecord(entries: { name: string; weight_g?: number }[]) {
    try {
      const selections = await Promise.all(entries.map(async (entry) => {
        const matches = await api.searchFoods(entry.name);
        const food = matches.find((item) => item.name === entry.name) ?? matches[0];
        return food ? { food, weight_g: entry.weight_g ?? food.default_weight_g ?? 100 } : null;
      }));
      const available = selections.filter((item): item is ManualMealSelection => Boolean(item));
      if (available.length === 0) throw new Error("没有找到可复用的食物");
      openManual(available);
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取最近食物失败");
    }
  }

  async function confirmProfileUpdate(payload: Partial<Profile>) {
    await api.updateProfile(payload);
    const proposal = await api.recalculateGoal();
    await api.confirmGoal(proposal.id);
    setShowProfileEdit(false);
    setNotice("档案和每日目标已更新");
    await refresh();
  }

  async function openPlans() {
    if (!profile?.safety_profile_reviewed) {
      setNotice("生成推荐前，请先确认过敏、绝对避免和不耐受信息；没有相关情况也可以直接保存空档案。");
      setShowProfileEdit(true);
      return;
    }
    try {
      const response = await api.previewPlans();
      setPlans(response.plans);
      setShowPlans(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "推荐失败");
    }
  }

  if (loading) return <div className="loading-screen"><Sparkles size={18} />正在准备你的今日饮食数据…</div>;
  if (!session) return <LoginPage onLogin={completeLogin} />;
  if (!profile?.onboarding_completed) {
    return <Onboarding onComplete={finishOnboarding} onLogout={logout} error={error} />;
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark"><Leaf size={18} strokeWidth={2.3} /><span>食见</span></div>
        <div className="header-meta"><span className="status-dot" />数据已同步<button className="logout-button" title="退出登录" onClick={logout}><UserRound size={15} />退出</button></div>
      </header>
      <main className="page">
        {error && <div className="toast error"><Info size={16} />{error}<button onClick={() => setError("")}><X size={14} /></button></div>}
        {notice && <div className="toast success"><Check size={16} />{notice}<button onClick={() => setNotice("")}><X size={14} /></button></div>}
        {tab === "today" && <TodayPage today={today} profile={profile} onRecord={() => openManual()} onPlans={openPlans} onNutritionInfo={() => setShowNutritionInfo(true)} onFeedback={setFeedbackMeal} />}
        {tab === "record" && <RecordPage today={today} onExample={openExampleDraft} onManual={() => openManual()} onQuick={openQuickRecord} draft={draft} onConfirm={confirmDraft} onCancel={() => setDraft(null)} />}
        {tab === "trends" && <TrendPage onAssessment={setAssessment} />}
        <div className="tab-panel" hidden={tab !== "assistant"}>
          <AssistantPage active={tab === "assistant"} today={today} onPlans={openPlans} onRecorded={async () => setToday(await api.getToday())} onProfileUpdated={refresh} />
        </div>
        {tab === "profile" && <ProfilePage profile={profile} goal={goal} onEdit={() => setShowProfileEdit(true)} onAccountDeleted={deleteAccount} />}
      </main>
      <nav className="bottom-nav">
        <NavButton active={tab === "today"} icon={<Home size={19} />} label="今日" onClick={() => setTab("today")} />
        <NavButton active={tab === "record"} icon={<ListPlus size={19} />} label="记录" onClick={() => setTab("record")} />
        <NavButton active={tab === "trends"} icon={<BarChart3 size={19} />} label="趋势" onClick={() => setTab("trends")} />
        <NavButton active={tab === "assistant"} icon={<MessageCircle size={19} />} label="AI助手" onClick={() => setTab("assistant")} />
        <NavButton active={tab === "profile"} icon={<Scale size={19} />} label="我的" onClick={() => setTab("profile")} />
      </nav>
      {showPlans && <PlanModal plans={plans} onClose={() => setShowPlans(false)} onSaved={(plan) => { setShowPlans(false); setNotice(`“${plan.title}”已保存为计划餐`); }} />}
      {showManualRecord && <ManualRecordModal initialItems={manualInitialItems} onClose={() => setShowManualRecord(false)} onSave={addManualMeal} />}
      {showNutritionInfo && <NutritionInfoModal today={today} onClose={() => setShowNutritionInfo(false)} />}
      {showProfileEdit && <ProfileEditModal profile={profile} currentGoal={goal} onClose={() => setShowProfileEdit(false)} onConfirm={confirmProfileUpdate} />}
      {feedbackMeal && <BodyFeedbackModal meal={feedbackMeal} onClose={() => setFeedbackMeal(null)} onSave={(feedback) => saveBodyFeedback(feedbackMeal, feedback)} />}
      {assessment && <AssessmentModal assessment={assessment} onClose={() => setAssessment(null)} />}
    </div>
  );
}

type ManualMealSelection = {
  food: Food;
  weight_g: number;
};

type CustomFoodForm = {
  name: string;
  brand: string;
  barcode: string;
  basis_weight_g: number;
  default_weight_g: number;
  energy_kcal: number;
  protein_g: number;
  fat_g: number;
  carbs_g: number;
  fiber_g: number;
  sodium_mg: number;
  sugars_g: number | null;
  added_sugar_g: number | null;
  allergens: string;
};

function foodSourceLabel(food: Food) {
  if (food.source === "user_confirmed_label") return "标签已确认";
  if (food.source === "open_food_facts") return "条码数据库";
  if (food.source === "user_nutrition_label") return "自定义标签";
  return "本地食物库";
}

function LoginPage({ onLogin }: { onLogin: (session: AuthSession) => Promise<void> }) {
  const [mode, setMode] = useState<"wechat" | "phone">("wechat");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [devCode, setDevCode] = useState("");
  const [countdown, setCountdown] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = window.setTimeout(() => setCountdown((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [countdown]);

  async function requestCode() {
    if (!/^1[3-9]\d{9}$/.test(phone)) {
      setError("请输入正确的 11 位手机号");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await api.requestPhoneCode(phone);
      setCountdown(60);
      setDevCode(response.dev_code ?? "");
      if (response.dev_code) setCode(response.dev_code);
    } catch (err) {
      setError(err instanceof Error ? err.message : "验证码发送失败");
    } finally {
      setLoading(false);
    }
  }

  async function loginWithPhone() {
    if (!/^1[3-9]\d{9}$/.test(phone) || !/^\d{6}$/.test(code)) {
      setError("请填写正确的手机号和 6 位验证码");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await onLogin(await api.phoneLogin(phone, code));
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
      setLoading(false);
    }
  }

  async function loginWithWechat() {
    setLoading(true);
    setError("");
    try {
      const wxRuntime = (window as Window & { wx?: { login: (options: { success: (result: { code?: string }) => void; fail: () => void }) => void } }).wx;
      const loginCode = wxRuntime
        ? await new Promise<string>((resolve, reject) => wxRuntime.login({ success: (result) => result.code ? resolve(result.code) : reject(new Error("未获得微信授权码")), fail: () => reject(new Error("微信授权失败")) }))
        : `dev-wechat-browser-${window.navigator.userAgent.slice(0, 24)}`;
      await onLogin(await api.wechatLogin(loginCode));
    } catch (err) {
      setError(err instanceof Error ? err.message : "微信登录失败");
      setLoading(false);
    }
  }

  return <div className="login-screen">
    <section className="login-panel">
      <div className="login-brand"><div className="brand-mark"><Leaf size={21} /><span>食记 AI</span></div><span>饮食记录与营养估算</span></div>
      <div className="login-heading"><span className="eyebrow">欢迎回来</span><h1>登录后，继续记录今天。</h1><p>你的饮食记录、目标和自定义食物会同步到当前账号。</p></div>
      <div className="login-tabs"><button className={mode === "wechat" ? "active" : ""} onClick={() => { setMode("wechat"); setError(""); }}>微信授权</button><button className={mode === "phone" ? "active" : ""} onClick={() => { setMode("phone"); setError(""); }}>手机号</button></div>
      {mode === "wechat" ? <div className="wechat-login"><div className="wechat-mark"><MessageCircle size={30} /></div><strong>使用微信授权登录</strong><span>微信小程序中将调用系统授权，不会获取你的微信密码。</span><button className="button wechat-button" disabled={loading} onClick={loginWithWechat}><MessageCircle size={18} />{loading ? "授权中…" : "微信授权登录"}</button></div> : <div className="phone-login">
        <label><span>手机号</span><input inputMode="numeric" maxLength={11} value={phone} onChange={(event) => setPhone(event.target.value.replace(/\D/g, ""))} placeholder="请输入手机号" /></label>
        <label><span>验证码</span><div className="code-input"><input inputMode="numeric" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} placeholder="6 位验证码" /><button disabled={loading || countdown > 0} onClick={requestCode}>{countdown > 0 ? `${countdown}s` : "获取验证码"}</button></div></label>
        {devCode && <div className="dev-code">本地开发验证码：<strong>{devCode}</strong></div>}
        <button className="button primary login-submit" disabled={loading} onClick={loginWithPhone}>{loading ? "登录中…" : "登录 / 注册"}</button>
      </div>}
      {error && <div className="inline-error" role="alert">{error}</div>}
      <div className="login-consent"><ShieldAlert size={14} />登录即表示你已阅读并同意《用户协议》和《隐私政策》；我们会保存你主动提交的饮食、体感、运动和档案数据。</div>
      <details className="privacy-summary"><summary>查看信息收集与使用说明</summary><p>我们收集你主动提交的身高、体重、年龄、饮食记录、过敏/不耐受、餐后体感和运动习惯，用于营养估算、膳食结构分析和个性化饮食建议。你可以在“我的”中导出或删除账户数据；生成 AI 回答所需文本可能由第三方 AI 服务处理。</p></details>
    </section>
  </div>;
}

function Onboarding({ onComplete, onLogout, error }: { onComplete: (payload: Partial<Profile>) => void; onLogout: () => void; error: string }) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<Partial<Profile>>({
    goal_pace: "standard",
  });
  const steps = [
    { label: "基础信息", caption: "先告诉我们一些基本情况" },
    { label: "身体数据", caption: "用来估算你的每日目标" },
    { label: "主要目标", caption: "一次只选一个主目标" },
    { label: "活动水平", caption: "不需要精确，选择最接近的" },
    { label: "饮食安全", caption: "没有相关情况可以留空，后续可随时修改" },
  ];
  const canNext = step === 0
    ? Boolean(form.sex && form.age && form.age >= 18 && form.age <= 64)
    : step === 1
      ? Boolean(form.height_cm && form.height_cm > 0 && form.height_cm <= 250 && form.current_weight_kg && form.current_weight_kg > 0 && form.current_weight_kg <= 400)
      : step === 2
        ? Boolean(form.primary_goal)
        : step === 3 ? Boolean(form.activity_level) : true;
  return (
    <div className="onboarding">
      <div className="onboarding-brand">
        <div className="brand-mark"><Leaf size={18} /><span>食见</span></div>
        <div className="onboarding-account"><span className="eyebrow">AI饮食管理</span><button className="logout-button" title="退出登录" onClick={onLogout}><UserRound size={15} />退出</button></div>
      </div>
      <div className="onboarding-copy">
        <span className="eyebrow">首次登录 · 约1分钟完成</span>
        <h1>先完善你的<br /><em>个人基础信息</em></h1>
        <p>这些信息只用于计算个性化饮食目标，完成后即可进入今日饮食管理。</p>
      </div>
      <div className="progress-track">{steps.map((item, index) => <span className={index <= step ? "active" : ""} key={item.label} />)}</div>
      <section className="onboarding-panel">
        <div className="panel-kicker">{step + 1} / {steps.length}</div>
        <h2>{steps[step].label}</h2>
        <p className="muted">{steps[step].caption}</p>
        {step === 0 && <div className="form-grid"><Field label="性别"><select value={form.sex ?? ""} onChange={(e) => setForm({ ...form, sex: e.target.value as Profile["sex"] })}><option value="" disabled>请选择</option><option value="male">男性</option><option value="female">女性</option></select></Field><Field label="年龄"><input type="number" min="18" max="64" placeholder="请输入年龄" value={form.age ?? ""} onChange={(e) => setForm({ ...form, age: e.target.value ? Number(e.target.value) : undefined })} /></Field></div>}
        {step === 1 && <div className="form-grid"><Field label="身高" suffix="cm"><input type="number" min="1" max="250" placeholder="请输入身高" value={form.height_cm ?? ""} onChange={(e) => setForm({ ...form, height_cm: e.target.value ? Number(e.target.value) : undefined })} /></Field><Field label="当前体重" suffix="kg"><input type="number" min="1" max="400" step="0.1" placeholder="请输入体重" value={form.current_weight_kg ?? ""} onChange={(e) => setForm({ ...form, current_weight_kg: e.target.value ? Number(e.target.value) : undefined })} /></Field></div>}
        {step === 2 && <OptionList value={form.primary_goal ?? ""} options={[["fat_loss", "减脂", "让体重趋势更稳地下降"], ["muscle_gain", "增肌", "把蛋白质和训练恢复放在前面"], ["maintain", "维持体重", "保持当前体重和饮食节奏"], ["structure", "改善饮食", "先把结构吃得更均衡"]]} onChange={(value) => setForm({ ...form, primary_goal: value as Profile["primary_goal"] })} />}
        {step === 3 && <OptionList value={form.activity_level ?? ""} options={[["sedentary", "久坐少动", "日常活动较少"], ["light", "轻度活动", "偶尔散步或轻运动"], ["moderate", "中度活动", "每周有规律运动"], ["high", "高活动量", "大部分时间都在运动或走动"]]} onChange={(value) => setForm({ ...form, activity_level: value as Profile["activity_level"] })} />}
        {step === 4 && <div className="onboarding-safety-grid">
          <SafetyProfileInput label="食物过敏" impact="可能涉及严重反应；推荐和保存会硬性拦截" value={form.allergies ?? []} placeholder="如 花生、虾" onChange={(allergies) => setForm({ ...form, allergies })} />
          <SafetyProfileInput label="绝对避免食物" impact="个人、宗教或伦理原因绝对不吃；推荐和保存会硬性拦截" value={form.hard_exclusions ?? []} placeholder="如 牛肉" onChange={(hard_exclusions) => setForm({ ...form, hard_exclusions })} />
          <SafetyProfileInput label="食物不耐受" impact="推荐会避开，记餐时提示；不等同于过敏" value={form.food_intolerances ?? []} placeholder="如 乳糖不耐受" onChange={(food_intolerances) => setForm({ ...form, food_intolerances })} />
          <SafetyProfileInput label="肠胃敏感" impact="仅用于个性化饮食提醒" value={form.digestive_sensitivities ?? []} placeholder="如 玉米吃多易腹胀" onChange={(digestive_sensitivities) => setForm({ ...form, digestive_sensitivities })} />
        </div>}
        {error && <div className="inline-error">{error}</div>}
        <div className="onboarding-actions"><button className="button ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>返回</button><button className="button primary" disabled={!canNext} onClick={() => step < steps.length - 1 ? setStep(step + 1) : onComplete({ ...form, safety_profile_reviewed: true })}>{step < steps.length - 1 ? <>下一步 <ArrowRight size={16} /></> : <>生成我的目标 <Sparkles size={16} /></>}</button></div>
      </section>
      <div className="onboarding-note"><ShieldAlert size={15} />本结果仅供日常饮食健康参考，不构成医疗诊断或治疗建议。如出现持续或严重不适，请及时咨询医生或注册营养师。</div>
    </div>
  );
}

function TodayPage({ today, profile, onRecord, onPlans, onNutritionInfo, onFeedback }: { today: TodaySummary | null; profile: Profile; onRecord: () => void; onPlans: () => void; onNutritionInfo: () => void; onFeedback: (meal: Meal) => void }) {
  const consumed = today?.consumed ?? emptyNutrition;
  const target = today?.target ?? emptyNutrition;
  const recordedMeals = today?.completeness.recorded_meals ?? 0;
  const expectedMeals = today?.completeness.expected_meals ?? 3;
  const scoreLabel = today?.score_status === "insufficient_data" ? "数据不足" : today?.score ? `${today.score}` : "进行中";
  const dateLabel = today?.date ? new Date(`${today.date}T00:00:00`).toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" }) : "今日";
  return <div className="content-stack">
    <section className="hero-row">
      <div><div className="eyebrow">{dateLabel} · {profile.primary_goal === "fat_loss" ? "减脂阶段" : "今日计划"}</div><h1>今天吃得怎么样？</h1><p className="hero-summary">{today?.gaps.length ? `当前最需要补的是${today.gaps.slice(0, 2).join("和")}。` : "先记录一餐，系统会帮你找到最重要的下一步。"}</p></div>
      <div className="score-orb"><span>{scoreLabel}</span><small>{today?.score ? "当前阶段评分" : "记录后生成"}</small></div>
    </section>
    <section className="dashboard-grid">
      <div className="panel calorie-panel"><div className="panel-heading"><div><span className="eyebrow">今日能量</span><h2>{Math.round(consumed.energy_kcal)} <small>/ {Math.round(target.energy_kcal)} kcal</small></h2></div><Flame className="accent-icon" size={20} /></div><div className="progress-line"><span style={{ width: `${Math.min((consumed.energy_kcal / Math.max(target.energy_kcal, 1)) * 100, 100)}%` }} /></div><div className="metric-foot"><span>已摄入</span><strong>还建议 {Math.max(0, Math.round(target.energy_kcal - consumed.energy_kcal))} kcal</strong></div></div>
      <div className="panel status-panel"><div className="panel-heading"><div><span className="eyebrow">记录完整度</span><h2>{recordedMeals}<small>{recordedMeals >= expectedMeals ? " 餐 · 已完整" : ` / ${expectedMeals} 餐`}</small></h2></div><Clock3 className="accent-icon yellow" size={20} /></div><p>{today?.score_status === "insufficient_data" ? "继续记录，评价会更可靠。" : recordedMeals >= expectedMeals ? "基础餐次已完整，加餐也会继续计入。" : "今天的数据正在逐步成形。"}</p><div className="mini-dots">{[0, 1, 2].map((dot) => <span className={dot < recordedMeals ? "filled" : ""} key={dot} />)}</div></div>
    </section>
    <section className="panel exercise-summary"><div className="panel-heading"><div><span className="eyebrow">今日运动</span><h2>{today?.exercise_count ?? 0}<small> 次 · {today?.exercise_duration_min ?? 0} 分钟</small></h2></div><Activity className="accent-icon" size={20} /></div><p>{today?.exercise_count ? `已记录约 ${Math.round(today.exercise_calories_burned)} 千卡运动消耗；消耗值仅作日常估算参考。` : "在 AI 助手中说“今天打了60分钟网球”，确认后即可计入。"}</p></section>
    <section className="section-block"><div className="section-title"><div><span className="eyebrow">营养缺口</span><h2>下一步补什么</h2></div><button className="icon-button" title="查看营养说明" onClick={onNutritionInfo}><Info size={17} /></button></div><div className="nutrient-grid"><NutrientBar label="蛋白质" current={consumed.protein_g} target={target.protein_g} unit="g" tone="teal" /><NutrientBar label="蔬菜" current={consumed.vegetable_g} target={target.vegetable_g} unit="g" tone="green" /><NutrientBar label="膳食纤维" current={consumed.fiber_g} target={target.fiber_g} unit="g" tone="gold" /></div></section>
    <section className="section-block"><div className="section-title"><div><span className="eyebrow">今日餐次</span><h2>饮食与餐后体感</h2></div><button className="text-button" onClick={onRecord}><Plus size={16} />记录一餐</button></div><div className="meal-list">{today?.meals.length ? today.meals.map((meal) => <MealRow meal={meal} key={meal.id} onFeedback={onFeedback} />) : <EmptyMeal onRecord={onRecord} />}</div></section>
    <button className="next-meal-cta" onClick={onPlans}><div className="cta-icon"><Sparkles size={19} /></div><div><strong>帮我安排下一餐</strong><span>根据今天剩余营养和你的场景，先预览再决定</span></div><ArrowRight size={19} /></button>
  </div>;
}

function NutrientBar({ label, current, target, unit, tone }: { label: string; current: number; target: number; unit: string; tone: string }) {
  const percent = Math.min((current / Math.max(target, 1)) * 100, 100);
  return <div className="nutrient"><div className="nutrient-heading"><span>{label}</span><strong>{Math.round(current)}<small> / {Math.round(target)}{unit}</small></strong></div><div className={`nutrient-track ${tone}`}><span style={{ width: `${percent}%` }} /></div><div className="nutrient-caption">{current >= target ? "已接近目标" : `还差约${Math.max(0, Math.round(target - current))}${unit}`}</div></div>;
}

function MealRow({ meal, onFeedback }: { meal: Meal; onFeedback: (meal: Meal) => void }) {
  const feedback = meal.body_feedback;
  return <div className="meal-row"><div className="meal-thumb">{meal.meal_type === "lunch" ? "午" : meal.meal_type === "dinner" ? "晚" : "餐"}</div><div className="meal-main"><div className="meal-name">{meal.items.map((item) => item.name).join("、")}</div><div className="meal-meta">约 {Math.round(meal.nutrition.energy_kcal)} kcal · 可信度{meal.confidence === "low" ? "低" : meal.confidence === "medium" ? "中" : "高"}</div>{feedback && <div className={`feedback-summary ${feedback.severity}`}>{feedback.symptoms.join("、") || "已记录体感"}</div>}</div><button className="feedback-button" onClick={() => onFeedback(meal)}><Activity size={15} />{feedback ? "更新体感" : "记录体感"}</button><div className="meal-score">{meal.score?.score ?? "--"}<small>评分</small></div></div>;
}

function EmptyMeal({ onRecord }: { onRecord: () => void }) {
  return <div className="empty-meal"><div className="empty-icon"><Utensils size={20} /></div><div><strong>还没有餐次记录</strong><span>从手动搜索添加食物，确认后再计入营养。</span></div><button className="button small" onClick={onRecord}><Search size={15} />手动记录</button></div>;
}

function RecordPage({ today, onExample, onManual, onQuick, draft, onConfirm, onCancel }: { today: TodaySummary | null; onExample: () => void; onManual: () => void; onQuick: (entries: { name: string; weight_g?: number }[]) => void; draft: MealDraft | null; onConfirm: (items: MealDraftItem[]) => Promise<void>; onCancel: () => void }) {
  const lastMeal = today?.meals[today.meals.length - 1];
  return <div className="content-stack"><div className="page-heading"><span className="eyebrow">记录</span><h1>把这一餐记下来</h1><p>手动搜索食物后确认份量；也可以打开固定示例，了解草稿确认流程。</p></div><div className="record-actions"><button className="record-action primary-record" onClick={onManual}><Search size={22} /><strong>手动搜索</strong><span>搜索食物并加入本餐</span></button><button className="record-action" onClick={onExample}><Sparkles size={21} /><strong>示例识别</strong><span>固定示例餐，仅演示确认流程</span></button></div><div className="section-block"><div className="section-title"><div><span className="eyebrow">快速复用</span><h2>最近使用</h2></div></div><div className="quick-chips"><button onClick={() => onQuick([{ name: "牛奶" }, { name: "燕麦" }])}>牛奶 + 燕麦</button><button onClick={() => onQuick([{ name: "鸡蛋" }, { name: "米饭" }])}>鸡蛋 + 米饭</button><button disabled={!lastMeal} onClick={() => onQuick(lastMeal?.items.map((item) => ({ name: item.name, weight_g: item.weight_g })) ?? [])}>复制上一餐</button></div></div>{draft && <DraftPanel draft={draft} onConfirm={onConfirm} onCancel={onCancel} />}</div>;
}

function ManualRecordModal({ initialItems, onClose, onSave }: { initialItems: ManualMealSelection[]; onClose: () => void; onSave: (items: ManualMealSelection[], mealType: MealType, bodyFeedback?: BodyFeedbackInput | null) => Promise<void> }) {
  const categories = ["常用", "主食", "肉蛋", "蔬菜", "水果", "奶豆", "常见菜"];
  const [query, setQuery] = useState("");
  const [barcode, setBarcode] = useState("");
  const [barcodeNotice, setBarcodeNotice] = useState("");
  const [results, setResults] = useState<Food[]>([]);
  const [selected, setSelected] = useState<ManualMealSelection[]>(initialItems);
  const [mealType, setMealType] = useState<MealType>(currentMealType());
  const [custom, setCustom] = useState<CustomFoodForm>({
    name: "",
    brand: "",
    barcode: "",
    basis_weight_g: 100,
    default_weight_g: 100,
    energy_kcal: 0,
    protein_g: 0,
    fat_g: 0,
    carbs_g: 0,
    fiber_g: 0,
    sodium_mg: 0,
    sugars_g: null,
    added_sugar_g: null,
    allergens: "",
  });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [bodySymptoms, setBodySymptoms] = useState<string[]>([]);
  const searchRequestId = useRef(0);

  async function search(searchQuery = query) {
    const requestId = ++searchRequestId.current;
    setLoading(true);
    setError("");
    try {
      const foods = await api.searchFoods(searchQuery === "常用" ? "" : searchQuery);
      if (requestId === searchRequestId.current) setResults(foods);
    } catch (err) {
      if (requestId === searchRequestId.current) setError(err instanceof Error ? err.message : "搜索失败");
    } finally {
      if (requestId === searchRequestId.current) setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => search(query), 220);
    return () => window.clearTimeout(timer);
  }, [query]);

  function addFood(food: Food) {
    setSaveError("");
    setSelected((current) => current.some((item) => item.food.id === food.id)
      ? current
      : [...current, { food, weight_g: food.default_weight_g ?? 100 }]);
  }

  async function lookupBarcode() {
    const normalized = barcode.trim();
    if (!/^\d{8,14}$/.test(normalized)) {
      setError("请输入 8 至 14 位商品条码");
      return;
    }
    setLoading(true);
    setError("");
    setBarcodeNotice("");
    try {
      const food = await api.lookupBarcode(normalized);
      setResults((current) => [food, ...current.filter((item) => item.id !== food.id)]);
      setBarcodeNotice(`已找到 ${food.brand ? `${food.brand} · ` : ""}${food.name}，请核对后添加`);
      setCustom((current) => ({
        ...current,
        name: food.name,
        brand: food.brand ?? "",
        barcode: normalized,
        basis_weight_g: 100,
        default_weight_g: food.default_weight_g ?? 100,
        energy_kcal: food.nutrition_per_100g.energy_kcal,
        protein_g: food.nutrition_per_100g.protein_g,
        fat_g: food.nutrition_per_100g.fat_g,
        carbs_g: food.nutrition_per_100g.carbs_g,
        fiber_g: food.nutrition_per_100g.fiber_g,
        sodium_mg: food.nutrition_per_100g.sodium_mg,
        sugars_g: food.nutrition_per_100g.sugars_g,
        added_sugar_g: food.nutrition_per_100g.added_sugar_g,
        allergens: food.allergens.join("、"),
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "条码查询失败，请按包装标签录入");
      setCustom({
        name: "",
        brand: "",
        barcode: normalized,
        basis_weight_g: 100,
        default_weight_g: 100,
        energy_kcal: 0,
        protein_g: 0,
        fat_g: 0,
        carbs_g: 0,
        fiber_g: 0,
        sodium_mg: 0,
        sugars_g: null,
        added_sugar_g: null,
        allergens: "",
      });
    } finally {
      setLoading(false);
    }
  }

  function updateWeight(foodId: string, weight: number) {
    setSaveError("");
    setSelected(selected.map((item) => item.food.id === foodId ? { ...item, weight_g: weight } : item));
  }

  async function createCustomFood() {
    if (!custom.name.trim()) {
      setError("请输入食物名称");
      return;
    }
    if (custom.basis_weight_g <= 0 || custom.default_weight_g <= 0) {
      setError("营养表基准重量和本次默认份量必须大于0");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const payload = {
        name: custom.name.trim(),
        basis_weight_g: custom.basis_weight_g,
        default_weight_g: custom.default_weight_g,
        nutrition: {
          energy_kcal: custom.energy_kcal,
          protein_g: custom.protein_g,
          fat_g: custom.fat_g,
          carbs_g: custom.carbs_g,
          fiber_g: custom.fiber_g,
          sodium_mg: custom.sodium_mg,
          sugars_g: custom.sugars_g,
          added_sugar_g: custom.added_sugar_g,
          vegetable_g: 0,
          fruit_g: 0,
        },
        allergens: custom.allergens.split(/[、,，;；]/).map((item) => item.trim()).filter(Boolean),
      };
      const food = custom.barcode || custom.brand
        ? await api.createLabelFood({ ...payload, barcode: custom.barcode || undefined, brand: custom.brand.trim() || undefined })
        : await api.createCustomFood(payload);
      setResults((current) => [food, ...current]);
      addFood(food);
      setCustom({
        name: "",
        brand: "",
        barcode: "",
        basis_weight_g: 100,
        default_weight_g: 100,
        energy_kcal: 0,
        protein_g: 0,
        fat_g: 0,
        carbs_g: 0,
        fiber_g: 0,
        sodium_mg: 0,
        sugars_g: null,
        added_sugar_g: null,
        allergens: "",
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建自定义食物失败");
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    if (selected.length === 0) {
      setSaveError("请至少添加一种食物");
      return;
    }
    if (selected.some((item) => !Number.isFinite(item.weight_g) || item.weight_g <= 0)) {
      setSaveError("食物份量必须大于 0g");
      return;
    }
    setSaveError("");
    setSaving(true);
    try {
      await onSave(selected, mealType, bodySymptoms.length ? {
        symptoms: bodySymptoms,
        severity: bodySymptoms.includes("无不适") ? "none" : "mild",
      } : null);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  }

  const activeCategory = !query ? "常用" : categories.includes(query) ? query : "";
  const resultLabel = activeCategory ? (activeCategory === "常用" ? "常用食材" : `${activeCategory}食材`) : "搜索结果";

  return (
    <div className="modal-backdrop">
      <section className="modal manual-modal">
        <div className="modal-header">
          <div><span className="eyebrow">手动记录</span><h2>搜索或录入食物</h2></div>
          <button className="icon-button" title="关闭" onClick={onClose}><X size={18} /></button>
        </div>

        <div className="manual-search">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") search(); }}
            placeholder="搜索名称或别名，如西红柿、地瓜、鸡肉"
          />
          <button className="button primary" onClick={() => search()} disabled={loading}><Search size={15} />{loading ? "搜索中" : "搜索"}</button>
        </div>
        <div className="barcode-lookup">
          <ScanBarcode size={19} />
          <div><strong>包装食品条码</strong><span>优先查询品牌营养数据，未收录时再按实物标签录入</span></div>
          <input inputMode="numeric" value={barcode} onChange={(event) => setBarcode(event.target.value.replace(/\D/g, ""))} onKeyDown={(event) => { if (event.key === "Enter") lookupBarcode(); }} placeholder="输入 8-14 位条码" aria-label="商品条码" />
          <button className="button ghost" disabled={loading} onClick={lookupBarcode}>{loading ? "查询中" : "查询"}</button>
        </div>
        {barcodeNotice && <div className="barcode-notice"><BadgeCheck size={15} />{barcodeNotice}</div>}
        <div className="meal-type-control" aria-label="选择餐次">
          {(Object.entries(mealTypeLabels) as [MealType, string][]).map(([value, label]) => <button className={mealType === value ? "active" : ""} key={value} onClick={() => setMealType(value)}>{label}</button>)}
        </div>
        <div className="food-categories">
          {categories.map((category) => <button className={activeCategory === category ? "active" : ""} key={category} onClick={() => setQuery(category === "常用" ? "" : category)}>{category}</button>)}
        </div>
        {error && <div className="inline-error">{error}</div>}

        <div className="manual-grid">
          <div className="manual-results">
            <div className="manual-column-heading"><span className="eyebrow">{resultLabel}</span><small>{results.length} 项</small></div>
            {results.length === 0
              ? <div className="empty-selected">{loading ? "正在加载常见食材" : "没有找到，试试别名或在下方自定义录入"}</div>
              : results.map((food) => {
                const isSelected = selected.some((item) => item.food.id === food.id);
                return <button className={`food-result ${isSelected ? "selected" : ""}`} disabled={isSelected} key={food.id} onClick={() => addFood(food)}>
                  <div><strong>{food.name}</strong><span>{food.brand ? `${food.brand} · ` : ""}{Math.round(food.nutrition_per_100g.energy_kcal)} kcal / 100g · 默认 {Math.round(food.default_weight_g ?? 100)}g</span><small className={`food-source ${food.source}`}>{foodSourceLabel(food)} · 可信度{food.confidence === "high" ? "高" : food.confidence === "medium" ? "中" : "低"}</small></div>
                  <span className="food-add-state">{isSelected ? <><Check size={14} />已添加</> : <><Plus size={15} />添加</>}</span>
                </button>;
              })}
          </div>

          <div className="manual-selected">
            <div className="manual-column-heading"><span className="eyebrow">本餐已添加</span><small>{selected.length} 项</small></div>
            {selected.length === 0
              ? <div className="empty-selected">点击左侧食材即可加入本餐</div>
              : selected.map((item) => <div className="selected-food" key={item.food.id}>
                <div><strong>{item.food.name}</strong><span>{Math.round(item.food.nutrition_per_100g.energy_kcal * item.weight_g / 100)} kcal · 可修改份量</span></div>
                <label><input type="number" min={1} value={item.weight_g} onChange={(event) => updateWeight(item.food.id, Number(event.target.value))} /><small>g</small></label>
                <button className="icon-button" title={`移除${item.food.name}`} onClick={() => setSelected(selected.filter((selectedItem) => selectedItem.food.id !== item.food.id))}><X size={15} /></button>
              </div>)}
          </div>
        </div>

        <div className="custom-food-panel">
          <div className="custom-food-heading"><FileText size={17} /><div><span className="eyebrow">营养成分表录入</span><strong>找不到食物时，按标签创建自定义食物</strong></div></div>
          <div className="custom-form">
            <label className="custom-field wide"><span>食物名称</span><input value={custom.name} onChange={(event) => setCustom({ ...custom, name: event.target.value })} placeholder="如 低脂酸奶" /></label>
            <label className="custom-field"><span>品牌（选填）</span><input value={custom.brand} onChange={(event) => setCustom({ ...custom, brand: event.target.value })} placeholder="包装品牌" /></label>
            <label className="custom-field"><span>条码（选填）</span><input inputMode="numeric" value={custom.barcode} onChange={(event) => setCustom({ ...custom, barcode: event.target.value.replace(/\D/g, "") })} placeholder="8-14 位" /></label>
            <label className="custom-field wide"><span>过敏原（选填）</span><input value={custom.allergens} onChange={(event) => setCustom({ ...custom, allergens: event.target.value })} placeholder="按包装标注填写，如 花生、牛奶" /></label>
            <label className="custom-field"><span>标签基准</span><input type="number" min={1} value={custom.basis_weight_g} onChange={(event) => setCustom({ ...custom, basis_weight_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>本次份量</span><input type="number" min={1} value={custom.default_weight_g} onChange={(event) => setCustom({ ...custom, default_weight_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>能量</span><input type="number" min={0} value={custom.energy_kcal} onChange={(event) => setCustom({ ...custom, energy_kcal: Number(event.target.value) })} /><small>kcal</small></label>
            <label className="custom-field"><span>蛋白质</span><input type="number" min={0} value={custom.protein_g} onChange={(event) => setCustom({ ...custom, protein_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>脂肪</span><input type="number" min={0} value={custom.fat_g} onChange={(event) => setCustom({ ...custom, fat_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>碳水</span><input type="number" min={0} value={custom.carbs_g} onChange={(event) => setCustom({ ...custom, carbs_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>膳食纤维</span><input type="number" min={0} value={custom.fiber_g} onChange={(event) => setCustom({ ...custom, fiber_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>钠</span><input type="number" min={0} value={custom.sodium_mg} onChange={(event) => setCustom({ ...custom, sodium_mg: Number(event.target.value) })} /><small>mg</small></label>
            <label className="custom-field"><span>总糖（标签有才填）</span><input type="number" min={0} value={custom.sugars_g ?? ""} onChange={(event) => setCustom({ ...custom, sugars_g: event.target.value === "" ? null : Number(event.target.value) })} placeholder="未标示留空" /><small>g</small></label>
            <label className="custom-field"><span>添加糖（标签有才填）</span><input type="number" min={0} value={custom.added_sugar_g ?? ""} onChange={(event) => setCustom({ ...custom, added_sugar_g: event.target.value === "" ? null : Number(event.target.value) })} placeholder="未标示留空" /><small>g</small></label>
          </div>
          <button className="button ghost custom-create-button" disabled={loading} onClick={createCustomFood}><Plus size={16} />{custom.barcode || custom.brand ? "确认标签并加入本餐" : "创建并加入本餐"}</button>
        </div>

        <div className="optional-feedback-inline">
          <div><Activity size={17} /><span><strong>餐后体感（选填）</strong><small>也可以吃完后再补录</small></span></div>
          <div className="symptom-chips">{symptomOptions.map((symptom) => <button className={bodySymptoms.includes(symptom) ? "selected" : ""} key={symptom} onClick={() => setBodySymptoms((current) => symptom === "无不适" ? (current.includes(symptom) ? [] : [symptom]) : current.includes(symptom) ? current.filter((item) => item !== symptom) : [...current.filter((item) => item !== "无不适"), symptom])}>{symptom}</button>)}</div>
        </div>

        <div className="manual-save-bar">
          {saveError && <div className="inline-error manual-save-error" role="alert"><Info size={15} />{saveError}</div>}
          <div className="modal-actions">
            <button className="button ghost" disabled={saving} onClick={onClose}>取消</button>
            <button className="button primary" aria-busy={saving} disabled={saving || selected.length === 0} onClick={save}><Check size={16} />{saving ? "正在保存…" : `保存本餐（${selected.length}项）`}</button>
          </div>
        </div>
      </section>
    </div>
  );
}

function DraftPanel({ draft, onConfirm, onCancel }: { draft: MealDraft; onConfirm: (items: MealDraftItem[]) => Promise<void>; onCancel: () => void }) {
  const [items, setItems] = useState(() => draft.items.map((item) => ({ ...item })));
  const [editingId, setEditingId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function updateItem(id: string, patch: Partial<MealDraftItem>) {
    setError("");
    setItems((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item));
  }

  async function save() {
    if (items.length === 0) {
      setError("请至少保留一种食物");
      return;
    }
    if (items.some((item) => !item.name.trim() || !Number.isFinite(item.estimated_weight_g) || item.estimated_weight_g <= 0)) {
      setError("食物名称不能为空，份量必须大于 0g");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onConfirm(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  }

  return <div className="modal-backdrop"><section className="modal draft-modal">
    <div className="modal-header"><div><span className="eyebrow">示例草稿 · 请确认</span><h2>固定示例餐，供演示确认流程</h2></div><button className="icon-button" title="关闭" disabled={saving} onClick={onCancel}><X size={18} /></button></div>
    <div className="confidence-note"><Sparkles size={15} />这不是照片或相机识别结果。你可以编辑食物和份量；确认后才会计算营养并保存。</div>
    <div className="draft-items">{items.map((item) => {
      const editing = editingId === item.id;
      return <div className={`draft-item ${editing ? "editing" : ""}`} key={item.id}>
        <div className="food-avatar">{item.name.slice(0, 1) || "食"}</div>
        <div className="draft-food">
          {editing ? <input aria-label={`修改${item.name}名称`} value={item.name} onChange={(event) => updateItem(item.id, { name: event.target.value })} /> : <strong>{item.name}</strong>}
          <span>示例份量 · {item.household_unit}</span>
          {editing && <label className="ratio-field">实际吃了<select value={item.consumed_ratio} onChange={(event) => updateItem(item.id, { consumed_ratio: Number(event.target.value) })}><option value={1}>全部</option><option value={0.75}>约四分之三</option><option value={0.5}>约一半</option><option value={0.25}>约四分之一</option></select></label>}
        </div>
        <div className="draft-weight"><input aria-label={`${item.name}克数`} type="number" min={1} value={item.estimated_weight_g} onChange={(event) => updateItem(item.id, { estimated_weight_g: Number(event.target.value) })} /><small>g</small></div>
        <div className="draft-item-actions"><button className="icon-button" title={editing ? "完成编辑" : "编辑食物"} onClick={() => setEditingId(editing ? "" : item.id)}>{editing ? <Check size={16} /> : <Pencil size={16} />}</button>{editing && <button className="icon-button danger" title={`移除${item.name}`} onClick={() => setItems((current) => current.filter((currentItem) => currentItem.id !== item.id))}><Trash2 size={15} /></button>}</div>
      </div>;
    })}</div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    <div className="modal-actions"><button className="button ghost" disabled={saving} onClick={onCancel}>先不保存</button><button className="button primary" aria-busy={saving} disabled={saving} onClick={save}><Check size={16} />{saving ? "正在计算…" : "确认并计算"}</button></div>
  </section></div>;
}

function PlanModal({ plans, onClose, onSaved }: { plans: MealPlan[]; onClose: () => void; onSaved: (plan: MealPlan) => void }) {
  const [options, setOptions] = useState(plans);
  const [selected, setSelected] = useState(0);
  const [saving, setSaving] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [error, setError] = useState("");
  const plan = options[selected];

  async function replaceProtein() {
    if (!plan) return;
    setReplacing(true);
    setError("");
    try {
      const updated = await api.replacePlan(plan.id, 0, plan.items[0].name === "鸡胸肉" ? "虾仁" : "鸡胸肉");
      setOptions((current) => current.map((item, index) => index === selected ? updated : item));
    } catch (err) {
      setError(err instanceof Error ? err.message : "替换失败，请稍后重试");
    } finally {
      setReplacing(false);
    }
  }

  async function save() {
    if (!plan) return;
    setSaving(true);
    setError("");
    try {
      const saved = await api.savePlan(plan.id);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : "计划保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  }

  return <div className="modal-backdrop"><section className="modal plan-modal">
    <div className="modal-header"><div><span className="eyebrow">餐前规划 · What-if</span><h2>先预览，再决定吃什么</h2></div><button className="icon-button" title="关闭" disabled={saving || replacing} onClick={onClose}><X size={18} /></button></div>
    <div className="plan-tabs">{options.map((item, index) => <button className={index === selected ? "active" : ""} key={item.id} onClick={() => { setSelected(index); setError(""); }}>{item.title}</button>)}</div>
    {plan && <div className="plan-detail"><div className="plan-summary"><div className="plan-plate"><Utensils size={26} /></div><div><h3>{plan.title}</h3><p>{plan.reason}</p></div></div><div className="plan-nutrition"><Metric label="能量" value={`${Math.round(plan.nutrition.energy_kcal)} kcal`} /><Metric label="蛋白质" value={`${Math.round(plan.nutrition.protein_g)} g`} /><Metric label="蔬菜" value={`${Math.round(plan.nutrition.vegetable_g)} g`} /></div><div className="plan-foods">{plan.items.map((item) => <div key={item.food_id ?? item.name}><span>{item.name}</span><strong>{item.weight_g}g</strong></div>)}</div>{plan.risks.length > 0 && <div className="risk-strip"><ShieldAlert size={16} />{plan.risks.map((risk) => presentSafetyRisk(risk, "recommendation")).join("；")}</div>}<div className="replace-row"><span>营养值为规则估算</span><button disabled={replacing} onClick={replaceProtein}>{replacing ? "替换中…" : "换一个蛋白质"}</button></div></div>}
    {error && <div className="inline-error" role="alert">{error}</div>}
    <div className="modal-actions"><button className="button ghost" disabled={saving || replacing} onClick={onClose}>继续看看</button><button className="button primary" disabled={saving || replacing} onClick={save}>{saving ? "保存中…" : <><Check size={16} />保存为计划餐</>}</button></div>
  </section></div>;
}

type AssistantMessage = RestorableAssistantMessage;

function agentAnswerLabel(response: AIChatResponse) {
  if (response.decision_stage === "clarify") return "需要补充信息";
  if (response.decision_stage === "propose") return "等待你确认";
  if (response.decision_stage === "safety") return "安全边界";
  if (response.kind === "food_nutrition") return "食物数据库";
  if (response.kind === "consumption_advice") return "计划摄入评估";
  if (response.kind === "dietary_knowledge") return "饮食知识";
  if (response.kind === "food_replacement") return "食物替换建议";
  return "基于今日记录";
}

function agentProviderLabel(response: AIChatResponse) {
  if (response.provider === "deepseek") return "DeepSeek";
  if (response.provider.startsWith("deepseek+")) return "DeepSeek + 后端校验";
  if (response.provider === "openai") return "OpenAI";
  if (response.provider.startsWith("openai+")) return "OpenAI + 后端校验";
  if (response.provider === "safety_guard") return "安全守卫";
  return "本地规则";
}

const agentToolLabels: Record<string, string> = {
  get_long_term_memory: "长期记忆召回",
  get_today_context: "今日数据",
  search_food: "食物查询",
  search_online_nutrition: "联网营养查询",
  convert_food_portion: "份量换算",
  calculate_nutrition: "营养计算",
  propose_meal_record: "待确认记餐",
  preview_meal_plans: "餐食预览",
  propose_memory: "待确认偏好",
  get_recent_meals: "近期记录",
};

function AssistantPage({ active, today, onPlans, onRecorded, onProfileUpdated }: { active: boolean; today: TodaySummary | null; onPlans: () => void; onRecorded: () => Promise<void>; onProfileUpdated: () => Promise<void> }) {
  const prompts = ["200g挂面的热量是多少？", "减脂可以吃主食吗？", "今天还能吃什么？", "午餐吃了150克米饭和两个鸡蛋"];
  const opening = today?.completeness.recorded_meals
    ? `我已经看到你今天记录的 ${today.completeness.recorded_meals} 餐。现在最值得关注的是${today.gaps.slice(0, 2).join("和") || "保持当前节奏"}。`
    : "今天还没有餐次记录。你可以查询食物营养、问饮食问题，或直接描述吃过的东西。";
  const [messages, setMessages] = useState<AssistantMessage[]>([{ id: "opening", role: "assistant", text: opening }]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [processingAction, setProcessingAction] = useState("");
  const [editingAction, setEditingAction] = useState<PendingActionEdit | null>(null);
  const [memories, setMemories] = useState<AgentMemory[]>([]);
  const [aiStatus, setAiStatus] = useState<AIServiceStatus | null>(null);
  const [feedback, setFeedback] = useState<Record<string, "helpful" | "not_helpful">>({});
  const [historyError, setHistoryError] = useState("");
  const [speechSupported, setSpeechSupported] = useState(true);
  const [speechStarting, setSpeechStarting] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [speechStatus, setSpeechStatus] = useState("");
  const threadEndRef = useRef<HTMLDivElement>(null);
  const threadViewportRef = useRef<HTMLElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const shouldFollowThread = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const lastTraceIdRef = useRef<string | null>(null);
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const isListeningRef = useRef(false);
  const restartTimerRef = useRef<number | null>(null);
  const voiceBaseRef = useRef("");
  const committedTranscriptRef = useRef("");
  const sessionFinalTranscriptRef = useRef("");
  const sessionInterimTranscriptRef = useRef("");
  const restartAttemptRef = useRef(0);
  const historyLoadingRef = useRef(false);
  const historyLoadedRef = useRef(false);

  function scrollThreadToLatest(behavior: ScrollBehavior = "smooth") {
    const viewport = threadViewportRef.current;
    if (!viewport) return;
    viewport.scrollTo({ top: viewport.scrollHeight, behavior });
    shouldFollowThread.current = true;
    setShowJumpToLatest(false);
  }

  function handleThreadScroll() {
    const viewport = threadViewportRef.current;
    if (!viewport) return;
    const nearLatest = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 88;
    shouldFollowThread.current = nearLatest;
    setShowJumpToLatest(!nearLatest);
  }

  useEffect(() => {
    if (!active) return;
    requestAnimationFrame(() => {
      composerRef.current?.scrollIntoView({ behavior: "auto", block: "nearest" });
      scrollThreadToLatest("auto");
    });
  }, [active]);

  useEffect(() => {
    if (shouldFollowThread.current) requestAnimationFrame(() => scrollThreadToLatest("smooth"));
  }, [messages, sending]);

  useEffect(() => {
    api.getAiMemories().then(setMemories).catch(() => undefined);
    api.getAiStatus().then(setAiStatus).catch(() => undefined);
    void loadHistory();
  }, []);

  async function loadHistory() {
    if (historyLoadingRef.current || historyLoadedRef.current) return;
    historyLoadingRef.current = true;
    setHistoryError("");
    try {
      const page = await api.getAiHistory(50);
      const restored = historyTurnsToMessages(page.items);
      setMessages((current) => mergeRestoredMessages(current, restored));
      shouldFollowThread.current = true;
      setFeedback((current) => ({
        ...current,
        ...Object.fromEntries(page.items
          .filter((turn) => turn.feedback_rating)
          .map((turn) => [turn.trace_id, turn.feedback_rating!])),
      }));
      lastTraceIdRef.current = page.items.at(-1)?.trace_id ?? lastTraceIdRef.current;
      historyLoadedRef.current = true;
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : "历史对话暂时无法恢复");
    } finally {
      historyLoadingRef.current = false;
    }
  }

  function speechErrorMessage(error: string) {
    if (error === "not-allowed" || error === "service-not-allowed") return "麦克风权限未开启。请在 Chrome/Edge 地址栏的网站权限中允许麦克风；本地使用 localhost，公网必须使用 HTTPS。";
    if (error === "no-speech") return "暂时没有听到语音，正在继续等待。也可以停止后手动输入。";
    if (error === "audio-capture") return "浏览器无法读取麦克风，请检查 Windows 输入设备，并确认麦克风未被其他程序占用。";
    if (error === "network") return "浏览器语音识别服务连接失败，请检查网络后重试，或直接手动输入。";
    if (error === "language-not-supported") return "当前浏览器不支持中文语音识别，请更新 Chrome/Edge 后重试。";
    return "语音识别失败，可以重试或手动修改文本。";
  }

  // Web Speech module: create exactly one recognition instance for this page.
  useEffect(() => {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      setSpeechSupported(false);
      setSpeechStatus("当前浏览器不支持 Web Speech。请使用 Chrome/Edge；Firefox 暂不支持此语音录入功能。你仍可手动输入文字。");
      return undefined;
    }
    setSpeechSupported(true);
    const recognition = new Recognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "zh-CN";
    recognitionRef.current = recognition;

    const joinTranscript = (...parts: string[]) => parts.map((part) => part.trim()).filter(Boolean).join(" ");
    const commitCurrentSession = () => {
      committedTranscriptRef.current = joinTranscript(
        committedTranscriptRef.current,
        sessionFinalTranscriptRef.current,
        sessionInterimTranscriptRef.current,
      );
      sessionFinalTranscriptRef.current = "";
      sessionInterimTranscriptRef.current = "";
      setInput(joinTranscript(voiceBaseRef.current, committedTranscriptRef.current));
    };
    const stopAfterRestartFailure = () => {
      isListeningRef.current = false;
      setSpeechStarting(false);
      setIsListening(false);
      setSpeechStatus("语音识别无法自动恢复，请点击“开始口述录音”重试。已有识别文字已保留。");
    };
    const resumeRecognition = () => {
      if (!isListeningRef.current || recognitionRef.current !== recognition) return;
      if (restartAttemptRef.current >= 3) {
        stopAfterRestartFailure();
        return;
      }
      restartAttemptRef.current += 1;
      const retryDelay = Math.min(250 * restartAttemptRef.current, 750);
      restartTimerRef.current = window.setTimeout(() => {
        restartTimerRef.current = null;
        if (!isListeningRef.current || recognitionRef.current !== recognition) return;
        try {
          recognition.start();
        } catch (error) {
          // Chrome may briefly report an active instance immediately after onend.
          if (error instanceof DOMException && error.name === "InvalidStateError") {
            resumeRecognition();
            return;
          }
          stopAfterRestartFailure();
        }
      }, retryDelay);
    };

    recognition.onstart = () => {
      setSpeechStarting(false);
      setIsListening(true);
      setSpeechStatus("正在口述录音，请直接描述吃了什么。");
    };
    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";
      for (let index = 0; index < event.results.length; index += 1) {
        const text = event.results[index][0]?.transcript ?? "";
        if (event.results[index].isFinal) finalText += text;
        else interimText += text;
      }
      sessionFinalTranscriptRef.current = finalText.trim();
      sessionInterimTranscriptRef.current = interimText.trim();
      restartAttemptRef.current = 0;
      setInput(joinTranscript(
        voiceBaseRef.current,
        committedTranscriptRef.current,
        sessionFinalTranscriptRef.current,
        sessionInterimTranscriptRef.current,
      ));
      setSpeechStatus("正在识别，你可以继续说，或点击停止后修改文字。");
    };
    recognition.onerror = (event) => {
      if (event.error === "aborted" && !isListeningRef.current) return;
      setSpeechStatus(speechErrorMessage(event.error));
      const fatalErrors = ["not-allowed", "service-not-allowed", "audio-capture", "network", "language-not-supported"];
      if (fatalErrors.includes(event.error)) {
        isListeningRef.current = false;
        setSpeechStarting(false);
        setIsListening(false);
      }
    };
    recognition.onend = () => {
      setSpeechStarting(false);
      commitCurrentSession();
      if (!isListeningRef.current) {
        setIsListening(false);
        setSpeechStatus((current) => current.startsWith("正在结束") ? "录音已停止，可以修改识别文字后发送。" : current);
        return;
      }

      // Chrome can end a continuous session after a pause; resume while the user still wants to record.
      setSpeechStatus("识别暂时中断，正在自动恢复录音…");
      resumeRecognition();
    };

    return () => {
      isListeningRef.current = false;
      if (restartTimerRef.current !== null) window.clearTimeout(restartTimerRef.current);
      recognition.onend = null;
      recognition.abort();
      recognitionRef.current = null;
    };
  }, []);

  function startSpeechRecognition() {
    if (!speechSupported || !recognitionRef.current) {
      setSpeechStatus("当前浏览器不支持 Web Speech，请使用 Chrome/Edge 或直接输入文字。");
      return;
    }
    if (!window.isSecureContext) {
      setSpeechStatus("语音输入仅支持 localhost 或 HTTPS。公网 HTTP 页面无法调用麦克风。");
      return;
    }
    if (isListeningRef.current || speechStarting || sending) return;

    voiceBaseRef.current = input.trim();
    committedTranscriptRef.current = "";
    sessionFinalTranscriptRef.current = "";
    sessionInterimTranscriptRef.current = "";
    restartAttemptRef.current = 0;
    isListeningRef.current = true;
    setSpeechStarting(true);
    setIsListening(true);
    setSpeechStatus("正在连接麦克风，请稍候…");
    try {
      recognitionRef.current.start();
    } catch (error) {
      setSpeechStarting(false);
      if (error instanceof DOMException && error.name === "InvalidStateError") {
        setSpeechStatus("语音识别已经在运行，请直接开始说话。");
        return;
      }
      isListeningRef.current = false;
      setIsListening(false);
      setSpeechStatus("录音启动失败。请确认使用 localhost/HTTPS、授予麦克风权限，并使用 Chrome/Edge。");
    }
  }

  function stopSpeechRecognition() {
    if (!recognitionRef.current || (!isListeningRef.current && !speechStarting)) return;
    isListeningRef.current = false;
    if (restartTimerRef.current !== null) {
      window.clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
    setSpeechStarting(false);
    setIsListening(false);
    setSpeechStatus("正在结束识别，请稍候。");
    try {
      recognitionRef.current.stop();
    } catch {
      recognitionRef.current.abort();
      setSpeechStatus("录音已停止，可以修改识别文字后发送。");
    }
  }

  useEffect(() => {
    if (!active && (isListeningRef.current || speechStarting)) stopSpeechRecognition();
  }, [active]);

  async function sendMessage(value = input, replyToTraceId?: string | null) {
    const message = value.trim();
    if (!message || sending) return;
    if (isListening) {
      setSpeechStatus("请先结束录音，检查识别文字后再发送。");
      return;
    }
    // Keep every turn linked to the latest server trace. Clarification buttons can
    // still explicitly target the trace they belong to, while ordinary follow-ups
    // such as “那这个呢” retain the current conversation automatically.
    const contextTraceId = replyToTraceId === undefined ? lastTraceIdRef.current : replyToTraceId;
    shouldFollowThread.current = true;
    setInput("");
    setMessages((current) => [...current, { id: `user-${Date.now()}`, role: "user", text: message }]);
    setSending(true);
    try {
      const response = await api.chat(message, contextTraceId);
      lastTraceIdRef.current = response.trace_id ?? lastTraceIdRef.current;
      setMessages((current) => appendMessageReconcilingAction(current, { id: `assistant-${Date.now()}`, role: "assistant", response }));
    } catch (err) {
      setMessages((current) => [...current, { id: `error-${Date.now()}`, role: "assistant", text: err instanceof Error ? err.message : "暂时无法处理这条消息" }]);
    } finally {
      setSending(false);
    }
  }

  function replaceAction(action: AIAction) {
    setMessages((current) => updateActionEverywhere(current, action));
    if (action.status !== "proposed") setEditingAction((current) => current?.actionId === action.id ? null : current);
  }

  function reconcileActionConflict(error: unknown) {
    if (!(error instanceof ApiError) || !error.detail?.current_action) return;
    replaceAction(error.detail.current_action);
  }

  function startActionEdit(action: AIAction) {
    if (action.status !== "proposed" || action.action_type !== "create_meal" || !action.payload.meal_type || !action.payload.items) return;
    setEditingAction({
      actionId: action.id,
      mealType: action.payload.meal_type,
      items: action.payload.items.map((item) => ({
        foodId: item.food_id,
        weightG: item.weight_g,
        consumedRatio: item.consumed_ratio,
      })),
    });
  }

  function updateEditingItem(foodId: string, field: "weightG" | "consumedRatio", value: number) {
    setEditingAction((current) => current ? {
      ...current,
      items: current.items.map((item) => item.foodId === foodId ? { ...item, [field]: value } : item),
    } : current);
  }

  async function saveActionEdit(response: AIChatResponse) {
    if (!response.action || response.action.status !== "proposed" || !editingAction || editingAction.actionId !== response.action.id) return;
    if (editingAction.items.some((item) => !Number.isFinite(item.weightG) || item.weightG <= 0 || !Number.isFinite(item.consumedRatio) || item.consumedRatio < 0 || item.consumedRatio > 1)) {
      setMessages((current) => [...current, { id: `edit-error-${Date.now()}`, role: "assistant", text: "请填写大于 0 的份量，并将实际食用比例控制在 0% 到 100% 之间。" }]);
      return;
    }
    setProcessingAction(response.action.id);
    try {
      const action = await api.updateAiAction(response.action.id, {
        meal_type: editingAction.mealType,
        items: editingAction.items.map((item) => ({
          food_id: item.foodId,
          weight_g: item.weightG,
          consumed_ratio: item.consumedRatio,
        })),
        expected_revision: response.action.revision,
      });
      replaceAction(action);
      setEditingAction(null);
    } catch (err) {
      reconcileActionConflict(err);
      setMessages((current) => [...current, { id: `edit-error-${Date.now()}`, role: "assistant", text: err instanceof Error ? err.message : "更新待确认记录失败，请稍后重试" }]);
    } finally {
      setProcessingAction("");
    }
  }

  async function confirmAction(response: AIChatResponse) {
    if (!response.action || response.action.status !== "proposed") return;
    setProcessingAction(response.action.id);
    try {
      const result = await api.confirmAiAction(response.action.id, response.action.revision);
      replaceAction(result.action);
      if (response.action.action_type === "create_meal") {
        await onRecorded();
        const recordedRisk = primaryRecordedWarning(result.meal?.risks ?? []);
        setMessages((current) => [...current, { id: `saved-${Date.now()}`, role: "assistant", text: recordedRisk
          ? `${presentSafetyRisk(recordedRisk, "recorded")}若出现明显不适，请及时就医。`
          : "已计入今日饮食。营养汇总和评分也已重新计算。" }]);
      } else if (response.action.action_type === "create_exercise") {
        await onRecorded();
        setMessages((current) => [...current, { id: `exercise-saved-${Date.now()}`, role: "assistant", text: "已计入今日运动。运动时长和消耗估算已同步到今日数据。" }]);
      } else if (response.action.action_type === "update_safety_profile") {
        await onProfileUpdated();
        setMessages((current) => [...current, { id: `safety-profile-${Date.now()}`, role: "assistant", text: "安全档案已更新。后续推荐、计划和保存会按对应风险等级校验，你也可以在“我的/档案”中修改或删除。" }]);
      } else if (result.memory) {
        setMemories((current) => current.some((item) => item.id === result.memory!.id) ? current : [...current, result.memory!]);
        setMessages((current) => [...current, { id: `remembered-${Date.now()}`, role: "assistant", text: "偏好已保存。后续建议会参考它，你可以在上方的记忆区随时删除。" }]);
      }
    } catch (err) {
      reconcileActionConflict(err);
      setMessages((current) => [...current, { id: `confirm-error-${Date.now()}`, role: "assistant", text: err instanceof Error ? err.message : "记录确认失败，请稍后重试" }]);
    } finally {
      setProcessingAction("");
    }
  }

  async function deleteMemory(memory: AgentMemory) {
    if (!window.confirm(`确定删除“${memory.value}”这条长期记忆吗？删除后将不再用于后续建议。`)) return;
    try {
      await api.deleteAiMemory(memory.id);
      setMemories((current) => current.filter((item) => item.id !== memory.id));
    } catch (err) {
      setMessages((current) => [...current, { id: `memory-error-${Date.now()}`, role: "assistant", text: err instanceof Error ? err.message : "删除偏好失败，请稍后重试" }]);
    }
  }

  function retryResponse(messageId: string) {
    const responseIndex = messages.findIndex((item) => item.id === messageId);
    const userMessage = [...messages.slice(0, responseIndex)].reverse().find((item) => item.role === "user" && item.text);
    if (userMessage?.text) void sendMessage(userMessage.text, null);
  }

  async function rateResponse(traceId: string, rating: "helpful" | "not_helpful") {
    if (feedback[traceId]) return;
    try {
      await api.giveAiFeedback(traceId, rating);
      setFeedback((current) => ({ ...current, [traceId]: rating }));
    } catch {
      setMessages((current) => [...current, { id: `feedback-error-${Date.now()}`, role: "assistant", text: "反馈暂时没有保存成功。" }]);
    }
  }

  async function cancelAction(response: AIChatResponse) {
    if (!response.action || response.action.status !== "proposed") return;
    setProcessingAction(response.action.id);
    try {
      const action = await api.cancelAiAction(response.action.id, response.action.revision);
      replaceAction(action);
    } catch (err) {
      reconcileActionConflict(err);
      setMessages((current) => [...current, { id: `cancel-error-${Date.now()}`, role: "assistant", text: err instanceof Error ? err.message : "取消失败，请稍后重试" }]);
    } finally {
      setProcessingAction("");
    }
  }

  function handleClarificationOption(message: AssistantMessage, option: string) {
    const response = message.response;
    if (!response) return;
    if (response.action?.status === "proposed" && option.includes("确认")) {
      void confirmAction(response);
      return;
    }
    void sendMessage(option, response.trace_id);
  }

  return <div className="content-stack assistant-page">
    <div className="page-heading"><span className="eyebrow">AI助手</span><h1>关于吃的，都可以问。</h1><p>查询食物营养、比较搭配或结合今日记录提问；任何数据写入都会先让你确认。</p></div>
    <div className="assistant-context"><div className="assistant-avatar"><Bot size={23} /></div><div><span className="eyebrow">当前上下文</span><strong>{today?.completeness.recorded_meals ?? 0} 餐已记录 · {Math.round(today?.remaining.energy_kcal ?? 0)} kcal 待安排</strong></div><div className="context-status"><span className={`model-status ${aiStatus?.available ? "online" : "rules"}`}>{aiStatus?.available ? "真实模型已配置" : "基础模式"}</span>{aiStatus?.online_food_search_available && <span className="source-status">联网营养源已接入</span>}<span className="context-confidence">数据可信度 {today?.confidence === "medium" ? "中" : today?.confidence === "high" ? "高" : "待完善"}</span></div></div>
    {historyError && <div className="agent-fallback history-restore-error" role="status"><Info size={14} /><span>历史对话暂时未恢复：{historyError}。你仍可继续发送新消息。</span><button type="button" onClick={() => void loadHistory()}><RefreshCw size={13} />重试</button></div>}
    <div className="agent-memory"><div className="memory-heading"><Brain size={17} /><div><strong>AI 记住的偏好</strong><span>仅保存你确认过的信息</span></div></div><div className="memory-items">{memories.length === 0 ? <span className="empty-memory">暂无</span> : memories.map((memory) => <span className="memory-chip" title={`来源：${memory.source_message} · ${new Date(memory.updated_at).toLocaleDateString("zh-CN")}`} key={memory.id}>{memory.category === "avoidance" ? "避免" : memory.category === "habit" ? "习惯" : "喜欢"}：{memory.value}<button title={`删除关于${memory.value}的记忆`} aria-label={`删除关于${memory.value}的记忆`} onClick={() => deleteMemory(memory)}><X size={13} /></button></span>)}</div></div>
    {messages.length <= 1 && <div className="prompt-grid">{prompts.map((prompt) => <button key={prompt} disabled={sending} onClick={() => sendMessage(prompt)}><span>{prompt}</span><ArrowRight size={15} /></button>)}</div>}
    <section className="assistant-thread" ref={threadViewportRef} onScroll={handleThreadScroll} aria-live="polite" aria-label="AI 对话记录">
      {messages.map((message, messageIndex) => {
        const actionId = message.response?.action?.id;
        const hasLaterActionCard = Boolean(actionId && messages.slice(messageIndex + 1).some((item) => item.response?.action?.id === actionId));
        return <div className={`chat-message ${message.role}`} key={message.id}>
        {message.role === "assistant" && <div className="message-avatar"><Bot size={16} /></div>}
        <div className="message-body">
          {message.text && <p>{message.text}</p>}
          {message.response && <>
            <div className={`agent-answer-meta ${message.response.decision_stage}`}><span>{agentAnswerLabel(message.response)}</span><span className="agent-provider">{agentProviderLabel(message.response)}</span><span>置信度 {message.response.confidence === "high" ? "高" : message.response.confidence === "medium" ? "中" : "低"}</span></div>
            <p>{message.response.message}</p>
            {message.response.basis.length > 0 && <div className="evidence-list">{message.response.basis.map((item) => <span key={item}><Check size={13} />{item}</span>)}</div>}
            {message.response.tool_calls.length > 0 && <div className="tool-audit"><span>数据过程</span><strong>{message.response.tool_calls.map((call) => call.name === "search_food" && call.result_summary?.includes("已优先联网") ? "联网营养查询" : agentToolLabels[call.name] ?? call.name).join(" · ")}</strong></div>}
            {message.response.fallback_used && <div className="agent-fallback"><Info size={13} /><span>{message.response.fallback_retryable ? "模型连接暂时中断，已保留输入并给出基础结果。" : "真实模型当前未配置或不可用，本次仅提供基础规则结果。"}</span>{message.response.fallback_retryable && <button type="button" disabled={sending} onClick={() => retryResponse(message.id)}><RefreshCw size={13} />重试</button>}</div>}
            {message.response.intent_conflict && <div className="agent-fallback"><ShieldAlert size={13} />已阻止将咨询、计划或假设内容写入饮食记录。</div>}
            {message.response.clarification_options.length > 0 && <div className="clarification-options">{message.response.clarification_options.map((option) => <button key={option} disabled={sending || Boolean(processingAction)} onClick={() => handleClarificationOption(message, option)}><span>{option}</span><ArrowRight size={14} /></button>)}</div>}
            {message.response.action && <div className={`ai-action-card ${message.response.action.status}`}>
              <div className="action-card-heading"><div><span className="eyebrow">{message.response.action.status === "proposed" ? "待确认动作" : "动作结果"}</span><strong>{message.response.action.title}</strong></div><FileText size={18} /></div>
              {message.response.action.action_type === "create_meal" ? <>
                <div className="action-foods">{message.response.action.payload.items?.map((item) => <div key={item.food_id}><span className="action-food-name"><span>{item.brand ? `${item.brand} ` : ""}{item.name}</span>{item.source_url && <a href={item.source_url} target="_blank" rel="noreferrer" title="查看原始营养资料">查看来源<ExternalLink size={11} /></a>}</span><strong>{item.estimated_weight_range_g?.length === 2 ? `${Math.round(item.estimated_weight_range_g[0])}-${Math.round(item.estimated_weight_range_g[1])}g` : `${Math.round(item.weight_g)}g`}</strong></div>)}</div>
                <div className="action-nutrition"><span>约 {Math.round(message.response.action.preview_nutrition.energy_kcal)} kcal</span><span>蛋白质 {Math.round(message.response.action.preview_nutrition.protein_g)}g</span>{message.response.action.payload.eaten_at && <span>{new Date(message.response.action.payload.eaten_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}</span>}</div>
                {message.response.action.payload.body_feedback && <div className="action-feedback"><Activity size={15} /><span>餐后体感：{message.response.action.payload.body_feedback.symptoms.join("、") || "无不适"}</span></div>}
                {message.response.action.payload.safety_risks?.map((risk) => <div className="risk-strip action-safety-risk" key={`${risk.type}-${risk.restriction}-${risk.food_name}`}><ShieldAlert size={15} /><span><strong>安全档案命中</strong>{presentSafetyRisk(risk, message.response!.action!.status === "proposed" ? "proposal" : "recorded")}</span></div>)}
                {!hasLaterActionCard && editingAction?.actionId === message.response.action.id && <div className="action-editor">
                  <label>餐次<select value={editingAction.mealType} onChange={(event) => setEditingAction((current) => current ? { ...current, mealType: event.target.value as MealType } : current)}>{(Object.keys(mealTypeLabels) as MealType[]).map((type) => <option key={type} value={type}>{mealTypeLabels[type]}</option>)}</select></label>
                  {message.response.action.payload.items?.map((item) => {
                    const editItem = editingAction.items.find((current) => current.foodId === item.food_id);
                    return <div className="action-editor-item" key={item.food_id}><strong>{item.name}</strong><label>份量<input type="number" min="1" max="10000" value={editItem?.weightG ?? item.weight_g} onChange={(event) => updateEditingItem(item.food_id, "weightG", Number(event.target.value))} /><span>g</span></label><label>实际食用<select value={editItem?.consumedRatio ?? item.consumed_ratio} onChange={(event) => updateEditingItem(item.food_id, "consumedRatio", Number(event.target.value))}><option value={1}>100%</option><option value={0.75}>75%</option><option value={0.5}>50%</option><option value={0.25}>25%</option><option value={0}>未吃</option></select></label></div>;
                  })}
                  <div className="action-editor-buttons"><button className="button ghost" disabled={Boolean(processingAction)} onClick={() => setEditingAction(null)}>放弃修改</button><button className="button primary" disabled={Boolean(processingAction)} onClick={() => saveActionEdit(message.response!)}><Check size={15} />{processingAction === message.response.action.id ? "更新中" : "更新预览"}</button></div>
                </div>}
              </> : <div className="memory-action-summary">{message.response.action.action_type === "update_safety_profile" ? <ShieldAlert size={16} /> : message.response.action.action_type === "create_exercise" ? <Activity size={16} /> : <Brain size={16} />}<span>{message.response.action.summary}</span></div>}
              {message.response.action.assumptions.length > 0 && <div className="action-assumptions"><strong>{message.response.action.action_type === "create_meal" ? "换算说明" : "影响范围"}</strong>{message.response.action.assumptions.map((item) => <span key={item}>{item}</span>)}</div>}
              {message.response.action.status === "proposed" ? hasLaterActionCard
                ? <div className="action-status"><RefreshCw size={15} />已同步到后续提案，请在最新卡片操作</div>
                : <div className="action-buttons">{message.response.action.action_type === "create_meal" && <button className="button ghost" disabled={Boolean(processingAction)} onClick={() => editingAction?.actionId === message.response!.action!.id ? setEditingAction(null) : startActionEdit(message.response!.action!)}><Pencil size={15} />{editingAction?.actionId === message.response.action.id ? "收起修改" : "修改"}</button>}<button className="button ghost" disabled={Boolean(processingAction)} onClick={() => cancelAction(message.response!)}>取消</button><button className="button primary" disabled={Boolean(processingAction)} onClick={() => confirmAction(message.response!)}><Check size={15} />{processingAction === message.response.action.id ? "处理中" : message.response.action.action_type === "create_meal" ? "确认记录" : message.response.action.action_type === "update_safety_profile" ? "确认更新档案" : "确认记住"}</button></div>
                : <div className="action-status"><Check size={15} />{message.response.action.status === "confirmed" ? message.response.action.action_type === "create_meal" ? "已确认并计入今日" : message.response.action.action_type === "create_exercise" ? "已确认并计入今日运动" : message.response.action.action_type === "update_safety_profile" ? "安全档案已更新" : "偏好已保存" : "已取消，未写入数据"}</div>}
            </div>}
            {message.response.cta === "preview_plans" && <button className="assistant-cta" onClick={onPlans}><Utensils size={17} /><span><strong>查看三种下一餐方案</strong><small>先预览营养，再决定是否保存</small></span><ArrowRight size={17} /></button>}
            {message.response.suggestions.length > 0 && <div className="message-suggestions">{message.response.suggestions.map((suggestion) => <button key={suggestion} onClick={() => sendMessage(suggestion)}>{suggestion}</button>)}</div>}
            {message.response.trace_id && <div className="agent-feedback"><span>{feedback[message.response.trace_id] ? "感谢反馈" : "这条回答有帮助吗？"}</span><button className={feedback[message.response.trace_id] === "helpful" ? "selected" : ""} disabled={Boolean(feedback[message.response.trace_id])} title="有帮助" aria-label="有帮助" onClick={() => rateResponse(message.response!.trace_id!, "helpful")}><ThumbsUp size={14} /></button><button className={feedback[message.response.trace_id] === "not_helpful" ? "selected" : ""} disabled={Boolean(feedback[message.response.trace_id])} title="没帮助" aria-label="没帮助" onClick={() => rateResponse(message.response!.trace_id!, "not_helpful")}><ThumbsDown size={14} /></button></div>}
          </>}
        </div>
      </div>;
      })}
      {sending && <div className="chat-message assistant"><div className="message-avatar"><Bot size={16} /></div><div className="message-body typing"><span /><span /><span /></div></div>}
      <div className="thread-end" ref={threadEndRef} />
      {showJumpToLatest && <button type="button" className="jump-to-latest" onClick={() => scrollThreadToLatest()}><ChevronDown size={15} />回到最新消息</button>}
    </section>
    <div ref={composerRef} className={`chat-composer ${isListening ? "listening" : ""}`}><div className="speech-controls"><button className="speech-button start" type="button" title={speechSupported ? "开始口述录音" : "当前浏览器不支持语音识别"} disabled={!speechSupported || isListening || speechStarting || sending} onClick={startSpeechRecognition}><Mic size={17} /><span>{speechStarting ? "连接中" : "开始口述录音"}</span></button><button className="speech-button stop" type="button" title="停止录音" disabled={!speechSupported || (!isListening && !speechStarting)} onClick={stopSpeechRecognition}><Square size={15} fill="currentColor" /><span>停止录音</span></button></div><input value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !isListening) sendMessage(); }} placeholder={isListening ? "正在识别，可以继续口述；停止后可修改文字" : "描述吃过的东西，或问今天怎么吃"} aria-label="语音识别结果或文字输入" /><button className="send-button" title={isListening ? "请先停止录音" : "发送"} disabled={!input.trim() || sending || isListening} onClick={() => sendMessage()}><Send size={17} /></button></div>
    {speechStatus && <div className={`speech-status ${isListening ? "active" : ""}`} role="alert" aria-live="polite"><Mic size={13} />{speechStatus}<button type="button" title="关闭提示" aria-label="关闭语音提示" onClick={() => setSpeechStatus("")}><X size={12} /></button></div>}
    <div className="assistant-boundary"><ShieldAlert size={14} />本结果仅供日常饮食健康参考，不构成医疗诊断或治疗建议。如出现持续或严重不适，请及时咨询医生或注册营养师。</div>
  </div>;
}

function TrendPage({ onAssessment }: { onAssessment: (assessment: DietAssessment) => void }) {
  const [trend, setTrend] = useState<SevenDayTrend | null>(null);
  const [loading, setLoading] = useState(true);
  const [assessing, setAssessing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getSevenDayTrend().then(setTrend).catch((err) => setError(err instanceof Error ? err.message : "趋势加载失败")).finally(() => setLoading(false));
  }, []);

  async function assessWeek() {
    setAssessing(true);
    setError("");
    try {
      onAssessment(await api.assessDiet({ days: 7 }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "评估生成失败");
    } finally {
      setAssessing(false);
    }
  }

  if (loading) return <div className="panel trend-loading">正在整理最近 7 天记录…</div>;
  return <div className="content-stack trend-page">
    <div className="page-heading"><span className="eyebrow">7天趋势</span><h1>看见饮食，也看见身体反应。</h1><p>关注食材多样性、记录完整度和餐后体感，不默认以减脂为目标。</p></div>
    {error && <div className="inline-error">{error}</div>}
    <section className="trend-metrics">
      <Metric label="已记录餐次" value={`${trend?.total_meals ?? 0} 餐`} />
      <Metric label="食材多样性" value={`${trend?.food_variety ?? 0} 种`} />
      <Metric label="体感记录率" value={`${trend?.feedback_completion_rate ?? 0}%`} />
    </section>
    {trend?.focus_action && <section className="weekly-focus"><div><span className="eyebrow">本周只做一件事</span><strong>{trend.focus_action}</strong></div>{trend.feedback_due_count > 0 && <span className="feedback-due">{trend.feedback_due_count} 餐待反馈</span>}</section>}
    <section className="section-block"><div className="section-title"><div><span className="eyebrow">每日记录</span><h2>最近一周</h2></div><span className="trend-average">均分 {trend?.average_score ?? "--"}</span></div><div className="week-chart">{trend?.days.map((day) => {
      const height = Math.max(8, Math.min(100, day.score ?? day.meal_count * 22));
      return <div className="week-column" key={day.date}><div className="week-bar-track"><span className={day.discomfort_count ? "has-discomfort" : ""} style={{ height: `${height}%` }} /></div><strong>{day.score ?? "--"}</strong><small>{new Date(`${day.date}T00:00:00`).toLocaleDateString("zh-CN", { weekday: "short" })}</small></div>;
    })}</div></section>
    <section className="section-block reaction-overview"><div className="section-title"><div><span className="eyebrow">身体反应</span><h2>这周常见体感</h2></div></div>{trend?.common_symptoms.length ? <div className="profile-tags">{trend.common_symptoms.map((item) => <span key={item}>{item}</span>)}</div> : <p className="empty-trend-copy">暂未形成重复体感信号。记录“无不适”同样有价值。</p>}</section>
    <section className="section-block"><div className="section-title"><div><span className="eyebrow">个人关联证据</span><h2>哪些只是观察，哪些重复出现</h2></div></div>{trend?.reaction_insights.length ? <div className="reaction-insight-list">{trend.reaction_insights.map((insight) => <article className="reaction-insight" key={`${insight.food_name}-${insight.symptom}`}><div><strong>{insight.food_name} · {insight.symptom}</strong><span className={`evidence-level ${insight.evidence_level}`}>{insight.evidence_level === "repeated" ? "重复关联" : insight.evidence_level === "suspected" ? "疑似关联" : "单次观察"}</span></div><p>{insight.summary}</p><small>{insight.occurrences} 次记录 · 最近一次 {new Date(`${insight.last_seen}T00:00:00`).toLocaleDateString("zh-CN")}</small></article>)}</div> : <p className="empty-trend-copy">还没有足够的饮食—体感配对。单次不适不会被系统直接判定为个人规律。</p>}</section>
    <button className="next-meal-cta" disabled={assessing || !trend?.total_meals} onClick={assessWeek}><div className="cta-icon"><Sparkles size={19} /></div><div><strong>{assessing ? "正在生成评估" : "生成7天饮食健康评估"}</strong><span>通用膳食标准 + 你的历史体感记忆</span></div><ArrowRight size={19} /></button>
  </div>;
}

function BodyFeedbackModal({ meal, onClose, onSave }: { meal: Meal; onClose: () => void; onSave: (feedback: BodyFeedbackInput) => Promise<void> }) {
  const [symptoms, setSymptoms] = useState<string[]>(meal.body_feedback?.symptoms ?? []);
  const [severity, setSeverity] = useState<BodyFeedbackInput["severity"]>(meal.body_feedback?.severity ?? "mild");
  const [minutes, setMinutes] = useState<number | "">(meal.body_feedback?.occurred_after_minutes ?? "");
  const [notes, setNotes] = useState(meal.body_feedback?.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function toggle(symptom: string) {
    setSymptoms((current) => symptom === "无不适"
      ? (current.includes(symptom) ? [] : [symptom])
      : current.includes(symptom)
        ? current.filter((item) => item !== symptom)
        : [...current.filter((item) => item !== "无不适"), symptom]);
  }

  async function save() {
    if (!symptoms.length) {
      setError("请选择至少一种体感，包括“无不适”");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSave({
        symptoms,
        severity: symptoms.includes("无不适") ? "none" : severity,
        occurred_after_minutes: minutes === "" ? null : minutes,
        notes: notes.trim() || null,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "体感保存失败");
      setSaving(false);
    }
  }

  return <div className="modal-backdrop"><section className="modal feedback-modal">
    <div className="modal-header"><div><span className="eyebrow">餐后体感</span><h2>{meal.items.map((item) => item.name).join("、")}</h2></div><button className="icon-button" title="关闭" disabled={saving} onClick={onClose}><X size={18} /></button></div>
    <div className="feedback-form"><label><span>身体感受</span><div className="symptom-chips">{symptomOptions.map((item) => <button className={symptoms.includes(item) ? "selected" : ""} key={item} onClick={() => toggle(item)}>{item}</button>)}</div></label>
      {!symptoms.includes("无不适") && <label><span>影响程度</span><div className="severity-control">{(["mild", "moderate", "severe"] as const).map((value) => <button className={severity === value ? "selected" : ""} key={value} onClick={() => setSeverity(value)}>{value === "mild" ? "轻微" : value === "moderate" ? "明显" : "严重"}</button>)}</div></label>}
      <Field label="餐后多久" suffix="分钟"><input type="number" min={0} max={10080} value={minutes} onChange={(event) => setMinutes(event.target.value ? Number(event.target.value) : "")} placeholder="选填" /></Field>
      <label className="feedback-note"><span>补充描述（选填）</span><textarea maxLength={300} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="例如：吃完约一小时开始胀气" /></label>
    </div>
    {severity === "severe" && !symptoms.includes("无不适") && <div className="confidence-note"><ShieldAlert size={15} />如不适持续、加重或伴随其他明显症状，请及时就医。</div>}
    {error && <div className="inline-error">{error}</div>}
    <div className="modal-actions"><button className="button ghost" disabled={saving} onClick={onClose}>取消</button><button className="button primary" disabled={saving} onClick={save}><Check size={16} />{saving ? "保存中" : "保存并生成评估"}</button></div>
  </section></div>;
}

function AssessmentModal({ assessment, onClose }: { assessment: DietAssessment; onClose: () => void }) {
  return <div className="modal-backdrop"><section className="modal assessment-modal">
    <div className="modal-header"><div><span className="eyebrow">饮食健康评估</span><h2>综合评分 {assessment.overall_score}/100</h2></div><button className="icon-button" title="关闭" onClick={onClose}><X size={18} /></button></div>
    <div className="assessment-sections">
      <section><span className="eyebrow">通用膳食分析</span>{assessment.general_analysis.map((item) => <p key={item}>{item}</p>)}</section>
      <section className="personalized"><span className="eyebrow">你的个人专属提示</span>{assessment.personalized_analysis.map((item) => <p key={item}>{item}</p>)}</section>
      {assessment.nutrition_estimates.length > 0 && <section><span className="eyebrow">营养估算区间</span><div className="nutrition-ranges">{assessment.nutrition_estimates.map((item) => <div key={item.nutrient}><span>{item.nutrient}</span><strong>{item.minimum}-{item.maximum} {item.unit}</strong><small>{item.basis}</small></div>)}</div></section>}
      <section><span className="eyebrow">优化建议</span>{assessment.optimization_suggestions.map((item) => <p key={item}>{item}</p>)}</section>
    </div>
    {assessment.fallback_used && <div className="agent-fallback"><Info size={13} />模型服务暂时不可用，本次由本地评估规则完成。</div>}
    <div className="assessment-disclaimer"><ShieldAlert size={15} />{assessment.disclaimer}</div>
    <div className="modal-actions"><button className="button primary" onClick={onClose}>知道了</button></div>
  </section></div>;
}

function NutritionInfoModal({ today, onClose }: { today: TodaySummary | null; onClose: () => void }) {
  return <div className="modal-backdrop"><section className="modal info-modal">
    <div className="modal-header"><div><span className="eyebrow">数据口径</span><h2>这些营养数值怎么来的</h2></div><button className="icon-button" title="关闭" onClick={onClose}><X size={18} /></button></div>
    <div className="info-list">
      <div><strong>已摄入</strong><span>根据你确认的食物、克数和实际食用比例，由后端按每 100g 营养数据换算。</span></div>
      <div><strong>每日目标</strong><span>依据身体数据、活动水平和主要目标生成；修改档案后需要再次确认才会生效。</span></div>
      <div><strong>可信度</strong><span>当前为{today?.confidence === "high" ? "高" : today?.confidence === "medium" ? "中" : "待完善"}。手动填写、AI 对话或示例草稿经确认后，结论会更可靠。</span></div>
    </div>
    <div className="confidence-note"><ShieldAlert size={15} />本结果仅供日常饮食健康参考，不构成医疗诊断或治疗建议。</div>
    <div className="modal-actions"><button className="button primary" onClick={onClose}>知道了</button></div>
  </section></div>;
}

function ProfileEditModal({ profile, currentGoal, onClose, onConfirm }: { profile: Profile; currentGoal: GoalProposal | null; onClose: () => void; onConfirm: (payload: Partial<Profile>) => Promise<void> }) {
  const [form, setForm] = useState<Partial<Profile>>({
    age: profile.age,
    height_cm: profile.height_cm,
    current_weight_kg: profile.current_weight_kg,
    activity_level: profile.activity_level,
    primary_goal: profile.primary_goal,
    goal_pace: profile.goal_pace ?? "standard",
    allergies: profile.allergies,
    hard_exclusions: profile.hard_exclusions,
    food_intolerances: profile.food_intolerances,
    digestive_sensitivities: profile.digestive_sensitivities,
    exercise_habits: profile.exercise_habits,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [allergies, setAllergies] = useState(profile.allergies);
  const [hardExclusions, setHardExclusions] = useState(profile.hard_exclusions);
  const [intolerances, setIntolerances] = useState(profile.food_intolerances);
  const [sensitivities, setSensitivities] = useState(profile.digestive_sensitivities);
  const [exerciseText, setExerciseText] = useState(profile.exercise_habits.join("、"));

  async function save() {
    if (!form.age || form.age < 18 || form.age > 64 || !form.height_cm || form.height_cm <= 0 || !form.current_weight_kg || form.current_weight_kg <= 0) {
      setError("请填写有效的年龄、身高和体重");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onConfirm({
        ...form,
        allergies,
        hard_exclusions: hardExclusions,
        food_intolerances: intolerances,
        digestive_sensitivities: sensitivities,
        exercise_habits: splitProfileTags(exerciseText),
        safety_profile_reviewed: true,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "档案更新失败，请稍后重试");
      setSaving(false);
    }
  }

  return <div className="modal-backdrop"><section className="modal profile-edit-modal">
    <div className="modal-header"><div><span className="eyebrow">档案与目标</span><h2>更新你的饮食档案</h2></div><button className="icon-button" title="关闭" disabled={saving} onClick={onClose}><X size={18} /></button></div>
    <div className="profile-edit-form">
      <Field label="年龄"><input type="number" min={18} max={64} value={form.age ?? ""} onChange={(event) => setForm({ ...form, age: Number(event.target.value) })} /></Field>
      <Field label="身高" suffix="cm"><input type="number" min={1} max={250} value={form.height_cm ?? ""} onChange={(event) => setForm({ ...form, height_cm: Number(event.target.value) })} /></Field>
      <Field label="当前体重" suffix="kg"><input type="number" min={1} max={400} step="0.1" value={form.current_weight_kg ?? ""} onChange={(event) => setForm({ ...form, current_weight_kg: Number(event.target.value) })} /></Field>
      <Field label="活动水平"><select value={form.activity_level ?? "light"} onChange={(event) => setForm({ ...form, activity_level: event.target.value as Profile["activity_level"] })}>{Object.entries(activityLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></Field>
      <Field label="主要目标"><select value={form.primary_goal ?? "structure"} onChange={(event) => setForm({ ...form, primary_goal: event.target.value as Profile["primary_goal"] })}>{Object.entries(goalLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></Field>
      <Field label="调整节奏"><select value={form.goal_pace ?? "standard"} onChange={(event) => setForm({ ...form, goal_pace: event.target.value as Profile["goal_pace"] })}><option value="steady">稳健</option><option value="standard">标准</option><option value="aggressive">积极</option></select></Field>
      <Field label="运动习惯"><input value={exerciseText} onChange={(event) => setExerciseText(event.target.value)} placeholder="如 每周打网球2次" /></Field>
      <div className="safety-edit-section">
        <div className="safety-edit-heading"><ShieldAlert size={17} /><div><strong>饮食安全档案</strong><span>点击标签旁的删除按钮即可移除；保存后立即影响推荐与拦截。</span></div></div>
        <SafetyProfileInput label="食物过敏" impact="高风险：推荐和保存硬性拦截" value={allergies} placeholder="如 花生、虾" onChange={setAllergies} />
        <SafetyProfileInput label="绝对避免食物" impact="硬性规则：推荐和保存硬性拦截" value={hardExclusions} placeholder="如 牛肉" onChange={setHardExclusions} />
        <SafetyProfileInput label="食物不耐受" impact="推荐避开；记餐提示，不等同于过敏" value={intolerances} placeholder="如 乳糖不耐受" onChange={setIntolerances} />
        <SafetyProfileInput label="肠胃敏感" impact="仅作个性化饮食提醒" value={sensitivities} placeholder="如 玉米吃多易腹胀" onChange={setSensitivities} />
      </div>
    </div>
    <div className="confidence-note"><Info size={15} />保存后系统会根据新档案重新计算并立即应用每日目标。当前能量目标为 {Math.round(currentGoal?.target.energy_kcal ?? 0)} kcal。</div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    <div className="modal-actions"><button className="button ghost" disabled={saving} onClick={onClose}>取消</button><button className="button primary" disabled={saving} onClick={save}><Check size={16} />{saving ? "保存并计算中…" : "保存并更新目标"}</button></div>
  </section></div>;
}

function ProfilePage({ profile, goal, onEdit, onAccountDeleted }: { profile: Profile; goal: GoalProposal | null; onEdit: () => void; onAccountDeleted: () => Promise<void> }) {
  const [privacyOpen, setPrivacyOpen] = useState(false);
  return <div className="content-stack">
    <div className="page-heading"><span className="eyebrow">我的</span><h1>你的饮食档案</h1><p>目标和建议会随着真实记录变化，但不会未经确认自动修改。</p></div>
    <section className="panel profile-summary"><div className="profile-avatar">{profile.age ?? "食"}</div><div><strong>{profile.age}岁 · {profile.height_cm}cm · {profile.current_weight_kg}kg</strong><span>{profile.primary_goal ? goalLabels[profile.primary_goal] : "未设置目标"} · {profile.activity_level ? activityLabels[profile.activity_level] : "未设置活动水平"}</span></div><button className="icon-button" title="编辑档案" onClick={onEdit}><Pencil size={16} /></button></section>
    <section className="section-block"><div className="section-title"><div><span className="eyebrow">当前目标</span><h2>每日建议</h2></div></div><div className="goal-metrics"><Metric label="能量" value={`${Math.round(goal?.target.energy_kcal ?? 0)} kcal`} /><Metric label="蛋白质" value={`${Math.round(goal?.target.protein_g ?? 0)} g`} /><Metric label="膳食纤维" value={`${Math.round(goal?.target.fiber_g ?? 0)} g`} /></div></section>
    <section className="section-block"><div className="section-title"><div><span className="eyebrow">确定性保护</span><h2>饮食安全档案</h2></div><button className="button ghost" onClick={onEdit}><Pencil size={15} />管理</button></div><div className="profile-factors safety-factors"><ProfileFactor label="食物过敏" impact="推荐和保存硬性拦截" values={profile.allergies} /><ProfileFactor label="绝对避免" impact="推荐和保存硬性拦截" values={profile.hard_exclusions} /><ProfileFactor label="食物不耐受" impact="推荐避开，记餐提示" values={profile.food_intolerances} /><ProfileFactor label="肠胃敏感" impact="个性化饮食提醒" values={profile.digestive_sensitivities} /></div></section>
    <section className="section-block"><div className="section-title"><div><span className="eyebrow">生活方式</span><h2>运动习惯</h2></div></div><div className="profile-tags">{profile.exercise_habits.length ? profile.exercise_habits.map((item) => <span key={item}>{item}</span>) : <small>暂无</small>}</div></section>
    <section className="privacy-panel"><button className={`privacy-row ${privacyOpen ? "open" : ""}`} onClick={() => setPrivacyOpen((value) => !value)}><ShieldAlert size={17} /><div><strong>你的数据由你控制</strong><span>档案、餐食、体感、计划和 AI 交互记录会保存到当前后端，用于同步和个性化分析。</span>{privacyOpen && <small>当前版本不上传或分析图片；示例草稿不包含用户图片。你可以导出自己的数据，或永久删除账号及其关联数据。</small>}</div><ChevronDown size={16} /></button>{privacyOpen && <AccountDataControls onAccountDeleted={onAccountDeleted} />}</section>
  </div>;
}

function AccountDataControls({ onAccountDeleted }: { onAccountDeleted: () => Promise<void> }) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [deleting, setDeleting] = useState(false);
  async function exportData() {
    setExporting(true);
    setExportError("");
    try {
      const data = await api.exportAccountData();
      const blob = new Blob([serializeAccountExport(data)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = accountExportFilename(data.exported_at);
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "导出失败，请稍后重试");
    } finally {
      setExporting(false);
    }
  }
  async function deleteData() {
    if (!isAccountDeleteConfirmation(confirmation)) return;
    setDeleting(true);
    try {
      await onAccountDeleted();
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "删除失败，请稍后重试");
      setDeleting(false);
    }
  }
  return <div className="account-data-controls"><div><strong>数据操作</strong><span>导出为 JSON，或永久删除本账号及全部个人数据。删除不可恢复。</span></div><div className="account-data-actions"><button className="button ghost" disabled={exporting || deleting} onClick={() => void exportData()}><Download size={15} />{exporting ? "导出中…" : "导出我的数据"}</button><button className="button danger" disabled={exporting || deleting} onClick={() => setDeleteOpen(true)}><Trash2 size={15} />删除账号</button></div>{exportError && <small className="inline-error" role="alert">{exportError}</small>}{deleteOpen && <div className="modal-backdrop"><section className="modal account-delete-modal"><div className="modal-header"><div><span className="eyebrow">不可恢复操作</span><h2>删除账号及全部数据</h2></div><button className="icon-button" title="关闭" disabled={deleting} onClick={() => setDeleteOpen(false)}><X size={18} /></button></div><p>这会删除档案、目标、餐食、体感、计划、自定义食品、AI 记忆和对话记录，并使当前登录失效。公共食物库不会被删除。</p><label className="field"><span>请输入“{ACCOUNT_DELETE_CONFIRMATION}”</span><div className="input-wrap"><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoFocus /></div></label><div className="modal-actions"><button className="button ghost" disabled={deleting} onClick={() => setDeleteOpen(false)}>取消</button><button className="button danger" disabled={deleting || !isAccountDeleteConfirmation(confirmation)} onClick={() => void deleteData()}>{deleting ? "删除中…" : "确认永久删除"}</button></div></section></div>}</div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function splitProfileTags(value: string) {
  return value.split(/[、,，;；]/).map((item) => item.trim()).filter(Boolean).slice(0, 12);
}

function SafetyProfileInput({ label, impact, value, placeholder, onChange }: { label: string; impact: string; value: string[]; placeholder: string; onChange: (value: string[]) => void }) {
  const [draftValue, setDraftValue] = useState("");
  function addValues() {
    const additions = splitProfileTags(draftValue);
    if (!additions.length) return;
    onChange(Array.from(new Set([...value, ...additions])).slice(0, 12));
    setDraftValue("");
  }
  return <div className="safety-profile-input">
    <div className="safety-input-label"><strong>{label}</strong><span>{impact}</span></div>
    <div className="safety-input-control"><input value={draftValue} placeholder={placeholder} onChange={(event) => setDraftValue(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addValues(); } }} /><button type="button" className="icon-button" title={`添加${label}`} aria-label={`添加${label}`} disabled={!draftValue.trim()} onClick={addValues}><Plus size={16} /></button></div>
    <div className="editable-profile-tags">{value.length ? value.map((item) => <span key={item}>{item}<button type="button" title={`删除${item}`} aria-label={`删除${item}`} onClick={() => onChange(value.filter((current) => current !== item))}><X size={12} /></button></span>) : <small>暂无，可留空</small>}</div>
  </div>;
}

function ProfileFactor({ label, impact, values }: { label: string; impact: string; values: string[] }) {
  return <div><div className="profile-factor-label"><strong>{label}</strong><small>{impact}</small></div><div className="profile-tags">{values.length ? values.map((item) => <span key={item}>{item}</span>) : <small>暂无</small>}</div></div>;
}

function Field({ label, suffix, children }: { label: string; suffix?: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span><div className="input-wrap">{children}{suffix && <small>{suffix}</small>}</div></label>;
}

function OptionList({ value, options, onChange }: { value: string; options: string[][]; onChange: (value: string) => void }) {
  return <div className="option-list">{options.map(([optionValue, title, caption]) => <button className={value === optionValue ? "selected" : ""} key={optionValue} onClick={() => onChange(optionValue)}><span className="option-radio">{value === optionValue && <Check size={13} />}</span><span><strong>{title}</strong><small>{caption}</small></span></button>)}</div>;
}

function NavButton({ active, icon, label, onClick }: { active: boolean; icon: React.ReactNode; label: string; onClick: () => void }) {
  return <button className={`nav-item ${active ? "active" : ""}`} onClick={onClick}>{icon}<span>{label}</span></button>;
}

export default App;
