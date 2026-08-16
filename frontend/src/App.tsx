import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Bot,
  Camera,
  Check,
  ChevronDown,
  Clock3,
  FileText,
  Flame,
  Home,
  Info,
  Leaf,
  ListPlus,
  MessageCircle,
  Pencil,
  Plus,
  Scale,
  Search,
  Send,
  ShieldAlert,
  Sparkles,
  Trash2,
  UserRound,
  Utensils,
  X,
} from "lucide-react";
import { AIChatResponse, api, AuthSession, Food, GoalProposal, Meal, MealDraft, MealDraftItem, MealPlan, MealType, Nutrition, Profile, TodaySummary, setAccessToken } from "./api";

type Tab = "today" | "record" | "assistant" | "profile";

const mealTypeLabels: Record<MealType, string> = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐", snack: "加餐" };
const goalLabels: Record<NonNullable<Profile["primary_goal"]>, string> = { fat_loss: "减脂", muscle_gain: "增肌", maintain: "维持体重", structure: "改善饮食" };
const activityLabels: Record<NonNullable<Profile["activity_level"]>, string> = { sedentary: "久坐少动", light: "轻度活动", moderate: "中度活动", high: "高活动量" };

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

  async function openDraft() {
    try {
      setDraft(await api.createDraft(currentMealType()));
      setTab("record");
    } catch (err) {
      setError(err instanceof Error ? err.message : "识别失败");
    }
  }

  async function confirmDraft(items: MealDraftItem[]) {
    if (!draft) return;
    await api.confirmDraft(draft, items);
    setDraft(null);
    setToday(await api.getToday());
    setNotice("餐次已保存，今日营养数据已更新");
    setTab("today");
  }

  async function addManualMeal(items: ManualMealSelection[], mealType: MealType) {
    await api.createManualMeal({
      meal_type: mealType,
      items: items.map((item) => ({
        food_id: item.food.id,
        weight_g: item.weight_g,
        consumed_ratio: 1,
      })),
    });
    setShowManualRecord(false);
    setToday(await api.getToday());
    setNotice(`${mealTypeLabels[mealType]}已保存`);
    setTab("today");
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
        {tab === "today" && <TodayPage today={today} profile={profile} onRecord={openDraft} onPlans={openPlans} onNutritionInfo={() => setShowNutritionInfo(true)} />}
        {tab === "record" && <RecordPage today={today} onRecord={openDraft} onManual={() => openManual()} onQuick={openQuickRecord} draft={draft} onConfirm={confirmDraft} onCancel={() => setDraft(null)} />}
        {tab === "assistant" && <AssistantPage today={today} onPlans={openPlans} onRecorded={async () => setToday(await api.getToday())} />}
        {tab === "profile" && <ProfilePage profile={profile} goal={goal} onEdit={() => setShowProfileEdit(true)} />}
      </main>
      <nav className="bottom-nav">
        <NavButton active={tab === "today"} icon={<Home size={19} />} label="今日" onClick={() => setTab("today")} />
        <NavButton active={tab === "record"} icon={<ListPlus size={19} />} label="记录" onClick={() => setTab("record")} />
        <NavButton active={tab === "assistant"} icon={<MessageCircle size={19} />} label="AI助手" onClick={() => setTab("assistant")} />
        <NavButton active={tab === "profile"} icon={<Scale size={19} />} label="我的" onClick={() => setTab("profile")} />
      </nav>
      {showPlans && <PlanModal plans={plans} onClose={() => setShowPlans(false)} onSaved={(plan) => { setShowPlans(false); setNotice(`“${plan.title}”已保存为计划餐`); }} />}
      {showManualRecord && <ManualRecordModal initialItems={manualInitialItems} onClose={() => setShowManualRecord(false)} onSave={addManualMeal} />}
      {showNutritionInfo && <NutritionInfoModal today={today} onClose={() => setShowNutritionInfo(false)} />}
      {showProfileEdit && <ProfileEditModal profile={profile} currentGoal={goal} onClose={() => setShowProfileEdit(false)} onConfirm={confirmProfileUpdate} />}
    </div>
  );
}

type ManualMealSelection = {
  food: Food;
  weight_g: number;
};

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
      <div className="login-brand"><div className="brand-mark"><Leaf size={21} /><span>食见</span></div><span>AI 饮食管理</span></div>
      <div className="login-heading"><span className="eyebrow">欢迎回来</span><h1>登录后，继续记录今天。</h1><p>你的饮食记录、目标和自定义食物会同步到当前账号。</p></div>
      <div className="login-tabs"><button className={mode === "wechat" ? "active" : ""} onClick={() => { setMode("wechat"); setError(""); }}>微信授权</button><button className={mode === "phone" ? "active" : ""} onClick={() => { setMode("phone"); setError(""); }}>手机号</button></div>
      {mode === "wechat" ? <div className="wechat-login"><div className="wechat-mark"><MessageCircle size={30} /></div><strong>使用微信授权登录</strong><span>微信小程序中将调用系统授权，不会获取你的微信密码。</span><button className="button wechat-button" disabled={loading} onClick={loginWithWechat}><MessageCircle size={18} />{loading ? "授权中…" : "微信授权登录"}</button></div> : <div className="phone-login">
        <label><span>手机号</span><input inputMode="numeric" maxLength={11} value={phone} onChange={(event) => setPhone(event.target.value.replace(/\D/g, ""))} placeholder="请输入手机号" /></label>
        <label><span>验证码</span><div className="code-input"><input inputMode="numeric" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} placeholder="6 位验证码" /><button disabled={loading || countdown > 0} onClick={requestCode}>{countdown > 0 ? `${countdown}s` : "获取验证码"}</button></div></label>
        {devCode && <div className="dev-code">本地开发验证码：<strong>{devCode}</strong></div>}
        <button className="button primary login-submit" disabled={loading} onClick={loginWithPhone}>{loading ? "登录中…" : "登录 / 注册"}</button>
      </div>}
      {error && <div className="inline-error" role="alert">{error}</div>}
      <div className="login-consent"><ShieldAlert size={14} />登录即表示你同意在本地 V1 环境保存账号和饮食数据。</div>
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
  ];
  const canNext = step === 0
    ? Boolean(form.sex && form.age && form.age >= 18 && form.age <= 64)
    : step === 1
      ? Boolean(form.height_cm && form.height_cm > 0 && form.height_cm <= 250 && form.current_weight_kg && form.current_weight_kg > 0 && form.current_weight_kg <= 400)
      : step === 2
        ? Boolean(form.primary_goal)
        : Boolean(form.activity_level);
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
        <div className="panel-kicker">{step + 1} / 4</div>
        <h2>{steps[step].label}</h2>
        <p className="muted">{steps[step].caption}</p>
        {step === 0 && <div className="form-grid"><Field label="性别"><select value={form.sex ?? ""} onChange={(e) => setForm({ ...form, sex: e.target.value as Profile["sex"] })}><option value="" disabled>请选择</option><option value="male">男性</option><option value="female">女性</option></select></Field><Field label="年龄"><input type="number" min="18" max="64" placeholder="请输入年龄" value={form.age ?? ""} onChange={(e) => setForm({ ...form, age: e.target.value ? Number(e.target.value) : undefined })} /></Field></div>}
        {step === 1 && <div className="form-grid"><Field label="身高" suffix="cm"><input type="number" min="1" max="250" placeholder="请输入身高" value={form.height_cm ?? ""} onChange={(e) => setForm({ ...form, height_cm: e.target.value ? Number(e.target.value) : undefined })} /></Field><Field label="当前体重" suffix="kg"><input type="number" min="1" max="400" step="0.1" placeholder="请输入体重" value={form.current_weight_kg ?? ""} onChange={(e) => setForm({ ...form, current_weight_kg: e.target.value ? Number(e.target.value) : undefined })} /></Field></div>}
        {step === 2 && <OptionList value={form.primary_goal ?? ""} options={[["fat_loss", "减脂", "让体重趋势更稳地下降"], ["muscle_gain", "增肌", "把蛋白质和训练恢复放在前面"], ["maintain", "维持体重", "保持当前体重和饮食节奏"], ["structure", "改善饮食", "先把结构吃得更均衡"]]} onChange={(value) => setForm({ ...form, primary_goal: value as Profile["primary_goal"] })} />}
        {step === 3 && <OptionList value={form.activity_level ?? ""} options={[["sedentary", "久坐少动", "日常活动较少"], ["light", "轻度活动", "偶尔散步或轻运动"], ["moderate", "中度活动", "每周有规律运动"], ["high", "高活动量", "大部分时间都在运动或走动"]]} onChange={(value) => setForm({ ...form, activity_level: value as Profile["activity_level"] })} />}
        {error && <div className="inline-error">{error}</div>}
        <div className="onboarding-actions"><button className="button ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>返回</button><button className="button primary" disabled={!canNext} onClick={() => step < 3 ? setStep(step + 1) : onComplete(form)}>{step < 3 ? <>下一步 <ArrowRight size={16} /></> : <>生成我的目标 <Sparkles size={16} /></>}</button></div>
      </section>
      <div className="onboarding-note"><ShieldAlert size={15} />这是饮食管理建议，不替代医疗诊断或治疗。</div>
    </div>
  );
}

function TodayPage({ today, profile, onRecord, onPlans, onNutritionInfo }: { today: TodaySummary | null; profile: Profile; onRecord: () => void; onPlans: () => void; onNutritionInfo: () => void }) {
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
    <section className="section-block"><div className="section-title"><div><span className="eyebrow">营养缺口</span><h2>下一步补什么</h2></div><button className="icon-button" title="查看营养说明" onClick={onNutritionInfo}><Info size={17} /></button></div><div className="nutrient-grid"><NutrientBar label="蛋白质" current={consumed.protein_g} target={target.protein_g} unit="g" tone="teal" /><NutrientBar label="蔬菜" current={consumed.vegetable_g} target={target.vegetable_g} unit="g" tone="green" /><NutrientBar label="膳食纤维" current={consumed.fiber_g} target={target.fiber_g} unit="g" tone="gold" /></div></section>
    <section className="section-block"><div className="section-title"><div><span className="eyebrow">今日餐次</span><h2>吃过的，和还没决定的</h2></div><button className="text-button" onClick={onRecord}><Plus size={16} />记录一餐</button></div><div className="meal-list">{today?.meals.length ? today.meals.map((meal) => <MealRow meal={meal} key={meal.id} />) : <EmptyMeal onRecord={onRecord} />}</div></section>
    <button className="next-meal-cta" onClick={onPlans}><div className="cta-icon"><Sparkles size={19} /></div><div><strong>帮我安排下一餐</strong><span>根据今天剩余营养和你的场景，先预览再决定</span></div><ArrowRight size={19} /></button>
  </div>;
}

function NutrientBar({ label, current, target, unit, tone }: { label: string; current: number; target: number; unit: string; tone: string }) {
  const percent = Math.min((current / Math.max(target, 1)) * 100, 100);
  return <div className="nutrient"><div className="nutrient-heading"><span>{label}</span><strong>{Math.round(current)}<small> / {Math.round(target)}{unit}</small></strong></div><div className={`nutrient-track ${tone}`}><span style={{ width: `${percent}%` }} /></div><div className="nutrient-caption">{current >= target ? "已接近目标" : `还差约${Math.max(0, Math.round(target - current))}${unit}`}</div></div>;
}

function MealRow({ meal }: { meal: Meal }) {
  return <div className="meal-row"><div className="meal-thumb">{meal.meal_type === "lunch" ? "午" : meal.meal_type === "dinner" ? "晚" : "餐"}</div><div className="meal-main"><div className="meal-name">{meal.items.map((item) => item.name).join("、")}</div><div className="meal-meta">{Math.round(meal.nutrition.energy_kcal)} kcal · 可信度{meal.confidence === "medium" ? "中" : "高"}</div></div><div className="meal-score">{meal.score?.score ?? "--"}<small>评分</small></div></div>;
}

function EmptyMeal({ onRecord }: { onRecord: () => void }) {
  return <div className="empty-meal"><div className="empty-icon"><Utensils size={20} /></div><div><strong>还没有餐次记录</strong><span>从一张照片开始，识别结果会先让你确认。</span></div><button className="button small" onClick={onRecord}><Camera size={15} />拍照记录</button></div>;
}

function RecordPage({ today, onRecord, onManual, onQuick, draft, onConfirm, onCancel }: { today: TodaySummary | null; onRecord: () => void; onManual: () => void; onQuick: (entries: { name: string; weight_g?: number }[]) => void; draft: MealDraft | null; onConfirm: (items: MealDraftItem[]) => Promise<void>; onCancel: () => void }) {
  const lastMeal = today?.meals[today.meals.length - 1];
  return <div className="content-stack"><div className="page-heading"><span className="eyebrow">记录</span><h1>把这一餐记下来</h1><p>先让系统识别，再由你确认份量和实际吃了多少。</p></div><div className="record-actions"><button className="record-action primary-record" onClick={onRecord}><Camera size={22} /><strong>拍照识别</strong><span>支持主照片和补充照片</span></button><button className="record-action" onClick={onManual}><Search size={21} /><strong>手动搜索</strong><span>从基础食物开始添加</span></button></div><div className="section-block"><div className="section-title"><div><span className="eyebrow">快速复用</span><h2>最近使用</h2></div></div><div className="quick-chips"><button onClick={() => onQuick([{ name: "牛奶" }, { name: "燕麦" }])}>牛奶 + 燕麦</button><button onClick={() => onQuick([{ name: "鸡蛋" }, { name: "米饭" }])}>鸡蛋 + 米饭</button><button disabled={!lastMeal} onClick={() => onQuick(lastMeal?.items.map((item) => ({ name: item.name, weight_g: item.weight_g })) ?? [])}>复制上一餐</button></div></div>{draft && <DraftPanel draft={draft} onConfirm={onConfirm} onCancel={onCancel} />}</div>;
}

function ManualRecordModal({ initialItems, onClose, onSave }: { initialItems: ManualMealSelection[]; onClose: () => void; onSave: (items: ManualMealSelection[], mealType: MealType) => Promise<void> }) {
  const categories = ["常用", "主食", "肉蛋", "蔬菜", "水果", "奶豆", "常见菜"];
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Food[]>([]);
  const [selected, setSelected] = useState<ManualMealSelection[]>(initialItems);
  const [mealType, setMealType] = useState<MealType>(currentMealType());
  const [custom, setCustom] = useState({
    name: "",
    basis_weight_g: 100,
    default_weight_g: 100,
    energy_kcal: 0,
    protein_g: 0,
    fat_g: 0,
    carbs_g: 0,
    fiber_g: 0,
    sodium_mg: 0,
    added_sugar_g: 0,
  });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");
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
      const food = await api.createCustomFood({
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
          added_sugar_g: custom.added_sugar_g,
          vegetable_g: 0,
          fruit_g: 0,
        },
      });
      setResults((current) => [food, ...current]);
      addFood(food);
      setCustom({
        name: "",
        basis_weight_g: 100,
        default_weight_g: 100,
        energy_kcal: 0,
        protein_g: 0,
        fat_g: 0,
        carbs_g: 0,
        fiber_g: 0,
        sodium_mg: 0,
        added_sugar_g: 0,
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
      await onSave(selected, mealType);
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
                  <div><strong>{food.name}</strong><span>{Math.round(food.nutrition_per_100g.energy_kcal)} kcal / 100g · 默认 {Math.round(food.default_weight_g ?? 100)}g{food.food_type === "custom" ? " · 自定义" : ""}</span></div>
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
            <label className="custom-field"><span>标签基准</span><input type="number" min={1} value={custom.basis_weight_g} onChange={(event) => setCustom({ ...custom, basis_weight_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>本次份量</span><input type="number" min={1} value={custom.default_weight_g} onChange={(event) => setCustom({ ...custom, default_weight_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>能量</span><input type="number" min={0} value={custom.energy_kcal} onChange={(event) => setCustom({ ...custom, energy_kcal: Number(event.target.value) })} /><small>kcal</small></label>
            <label className="custom-field"><span>蛋白质</span><input type="number" min={0} value={custom.protein_g} onChange={(event) => setCustom({ ...custom, protein_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>脂肪</span><input type="number" min={0} value={custom.fat_g} onChange={(event) => setCustom({ ...custom, fat_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>碳水</span><input type="number" min={0} value={custom.carbs_g} onChange={(event) => setCustom({ ...custom, carbs_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>膳食纤维</span><input type="number" min={0} value={custom.fiber_g} onChange={(event) => setCustom({ ...custom, fiber_g: Number(event.target.value) })} /><small>g</small></label>
            <label className="custom-field"><span>钠</span><input type="number" min={0} value={custom.sodium_mg} onChange={(event) => setCustom({ ...custom, sodium_mg: Number(event.target.value) })} /><small>mg</small></label>
            <label className="custom-field"><span>添加糖</span><input type="number" min={0} value={custom.added_sugar_g} onChange={(event) => setCustom({ ...custom, added_sugar_g: Number(event.target.value) })} /><small>g</small></label>
          </div>
          <button className="button ghost custom-create-button" disabled={loading} onClick={createCustomFood}><Plus size={16} />创建并加入本餐</button>
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
    <div className="modal-header"><div><span className="eyebrow">识别草稿 · 请确认</span><h2>这顿看起来有这些</h2></div><button className="icon-button" title="关闭" disabled={saving} onClick={onCancel}><X size={18} /></button></div>
    <div className="confidence-note"><Sparkles size={15} />识别结果不会直接计入今日数据，确认后才会计算营养。</div>
    <div className="draft-items">{items.map((item) => {
      const editing = editingId === item.id;
      return <div className={`draft-item ${editing ? "editing" : ""}`} key={item.id}>
        <div className="food-avatar">{item.name.slice(0, 1) || "食"}</div>
        <div className="draft-food">
          {editing ? <input aria-label={`修改${item.name}名称`} value={item.name} onChange={(event) => updateItem(item.id, { name: event.target.value })} /> : <strong>{item.name}</strong>}
          <span>识别份量 · {item.household_unit}</span>
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
    {plan && <div className="plan-detail"><div className="plan-summary"><div className="plan-plate"><Utensils size={26} /></div><div><h3>{plan.title}</h3><p>{plan.reason}</p></div></div><div className="plan-nutrition"><Metric label="能量" value={`${Math.round(plan.nutrition.energy_kcal)} kcal`} /><Metric label="蛋白质" value={`${Math.round(plan.nutrition.protein_g)} g`} /><Metric label="蔬菜" value={`${Math.round(plan.nutrition.vegetable_g)} g`} /></div><div className="plan-foods">{plan.items.map((item) => <div key={item.food_id ?? item.name}><span>{item.name}</span><strong>{item.weight_g}g</strong></div>)}</div>{plan.risks.length > 0 && <div className="risk-strip"><ShieldAlert size={16} />{plan.risks.map((risk) => risk.message).join("；")}</div>}<div className="replace-row"><span>营养值为规则估算</span><button disabled={replacing} onClick={replaceProtein}>{replacing ? "替换中…" : "换一个蛋白质"}</button></div></div>}
    {error && <div className="inline-error" role="alert">{error}</div>}
    <div className="modal-actions"><button className="button ghost" disabled={saving || replacing} onClick={onClose}>继续看看</button><button className="button primary" disabled={saving || replacing} onClick={save}>{saving ? "保存中…" : <><Check size={16} />保存为计划餐</>}</button></div>
  </section></div>;
}

type AssistantMessage = {
  id: string;
  role: "user" | "assistant";
  text?: string;
  response?: AIChatResponse;
};

function AssistantPage({ today, onPlans, onRecorded }: { today: TodaySummary | null; onPlans: () => void; onRecorded: () => Promise<void> }) {
  const prompts = ["今天还能吃什么？", "为什么蛋白质不足？", "鸡胸肉能换什么？", "午餐吃了150克米饭和两个鸡蛋"];
  const opening = today?.completeness.recorded_meals
    ? `我已经看到你今天记录的 ${today.completeness.recorded_meals} 餐。现在最值得关注的是${today.gaps.slice(0, 2).join("和") || "保持当前节奏"}。`
    : "今天还没有餐次记录。你可以直接描述吃过的东西，我会先整理成待确认记录。";
  const [messages, setMessages] = useState<AssistantMessage[]>([{ id: "opening", role: "assistant", text: opening }]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [processingAction, setProcessingAction] = useState("");
  const threadEndRef = useRef<HTMLDivElement>(null);
  const shouldFollowThread = useRef(false);

  useEffect(() => {
    if (shouldFollowThread.current) threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  async function sendMessage(value = input) {
    const message = value.trim();
    if (!message || sending) return;
    shouldFollowThread.current = true;
    setInput("");
    setMessages((current) => [...current, { id: `user-${Date.now()}`, role: "user", text: message }]);
    setSending(true);
    try {
      const response = await api.chat(message);
      setMessages((current) => [...current, { id: `assistant-${Date.now()}`, role: "assistant", response }]);
    } catch (err) {
      setMessages((current) => [...current, { id: `error-${Date.now()}`, role: "assistant", text: err instanceof Error ? err.message : "暂时无法处理这条消息" }]);
    } finally {
      setSending(false);
    }
  }

  function updateAction(messageId: string, status: "confirmed" | "cancelled") {
    setMessages((current) => current.map((message) => message.id === messageId && message.response?.action
      ? { ...message, response: { ...message.response, action: { ...message.response.action, status } } }
      : message));
  }

  async function confirmAction(messageId: string, response: AIChatResponse) {
    if (!response.action) return;
    setProcessingAction(response.action.id);
    try {
      await api.confirmAiAction(response.action.id);
      updateAction(messageId, "confirmed");
      await onRecorded();
      setMessages((current) => [...current, { id: `saved-${Date.now()}`, role: "assistant", text: "已计入今日饮食。营养汇总和评分也已重新计算。" }]);
    } catch (err) {
      setMessages((current) => [...current, { id: `confirm-error-${Date.now()}`, role: "assistant", text: err instanceof Error ? err.message : "记录确认失败，请稍后重试" }]);
    } finally {
      setProcessingAction("");
    }
  }

  async function cancelAction(messageId: string, response: AIChatResponse) {
    if (!response.action) return;
    setProcessingAction(response.action.id);
    try {
      await api.cancelAiAction(response.action.id);
      updateAction(messageId, "cancelled");
    } catch (err) {
      setMessages((current) => [...current, { id: `cancel-error-${Date.now()}`, role: "assistant", text: err instanceof Error ? err.message : "取消失败，请稍后重试" }]);
    } finally {
      setProcessingAction("");
    }
  }

  return <div className="content-stack assistant-page">
    <div className="page-heading"><span className="eyebrow">AI助手</span><h1>下一步，吃得更明白。</h1><p>读取今日记录并提出可执行建议；任何写入都会先让你确认。</p></div>
    <div className="assistant-context"><div className="assistant-avatar"><Bot size={23} /></div><div><span className="eyebrow">当前上下文</span><strong>{today?.completeness.recorded_meals ?? 0} 餐已记录 · {Math.round(today?.remaining.energy_kcal ?? 0)} kcal 待安排</strong></div><span className="context-confidence">数据可信度 {today?.confidence === "medium" ? "中" : today?.confidence === "high" ? "高" : "待完善"}</span></div>
    <div className="prompt-grid">{prompts.map((prompt) => <button key={prompt} disabled={sending} onClick={() => sendMessage(prompt)}><span>{prompt}</span><ArrowRight size={15} /></button>)}</div>
    <section className="assistant-thread" aria-live="polite">
      {messages.map((message) => <div className={`chat-message ${message.role}`} key={message.id}>
        {message.role === "assistant" && <div className="message-avatar"><Bot size={16} /></div>}
        <div className="message-body">
          {message.text && <p>{message.text}</p>}
          {message.response && <>
            <p>{message.response.message}</p>
            {message.response.basis.length > 0 && <div className="evidence-list">{message.response.basis.map((item) => <span key={item}><Check size={13} />{item}</span>)}</div>}
            {message.response.action && <div className={`ai-action-card ${message.response.action.status}`}>
              <div className="action-card-heading"><div><span className="eyebrow">待确认动作</span><strong>{message.response.action.title}</strong></div><FileText size={18} /></div>
              <div className="action-foods">{message.response.action.payload.items.map((item) => <div key={item.food_id}><span>{item.name}</span><strong>{Math.round(item.weight_g)}g</strong></div>)}</div>
              <div className="action-nutrition"><span>约 {Math.round(message.response.action.preview_nutrition.energy_kcal)} kcal</span><span>蛋白质 {Math.round(message.response.action.preview_nutrition.protein_g)}g</span></div>
              {message.response.action.status === "proposed" ? <div className="action-buttons"><button className="button ghost" disabled={Boolean(processingAction)} onClick={() => cancelAction(message.id, message.response!)}>取消</button><button className="button primary" disabled={Boolean(processingAction)} onClick={() => confirmAction(message.id, message.response!)}><Check size={15} />{processingAction === message.response.action.id ? "处理中" : "确认记录"}</button></div> : <div className="action-status"><Check size={15} />{message.response.action.status === "confirmed" ? "已确认并计入今日" : "已取消，未写入数据"}</div>}
            </div>}
            {message.response.cta === "preview_plans" && <button className="assistant-cta" onClick={onPlans}><Utensils size={17} /><span><strong>查看三种下一餐方案</strong><small>先预览营养，再决定是否保存</small></span><ArrowRight size={17} /></button>}
            {message.response.suggestions.length > 0 && <div className="message-suggestions">{message.response.suggestions.map((suggestion) => <button key={suggestion} onClick={() => sendMessage(suggestion)}>{suggestion}</button>)}</div>}
          </>}
        </div>
      </div>)}
      {sending && <div className="chat-message assistant"><div className="message-avatar"><Bot size={16} /></div><div className="message-body typing"><span /><span /><span /></div></div>}
      <div className="thread-end" ref={threadEndRef} />
    </section>
    <div className="chat-composer"><input value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") sendMessage(); }} placeholder="描述吃过的东西，或问今天怎么吃" aria-label="向AI助手提问" /><button className="send-button" title="发送" disabled={!input.trim() || sending} onClick={() => sendMessage()}><Send size={17} /></button></div>
    <div className="assistant-boundary"><ShieldAlert size={14} />饮食管理建议不替代医疗诊断；营养数值由规则引擎计算。</div>
  </div>;
}

function NutritionInfoModal({ today, onClose }: { today: TodaySummary | null; onClose: () => void }) {
  return <div className="modal-backdrop"><section className="modal info-modal">
    <div className="modal-header"><div><span className="eyebrow">数据口径</span><h2>这些营养数值怎么来的</h2></div><button className="icon-button" title="关闭" onClick={onClose}><X size={18} /></button></div>
    <div className="info-list">
      <div><strong>已摄入</strong><span>根据你确认的食物、克数和实际食用比例，由后端按每 100g 营养数据换算。</span></div>
      <div><strong>每日目标</strong><span>依据身体数据、活动水平和主要目标生成；修改档案后需要再次确认才会生效。</span></div>
      <div><strong>可信度</strong><span>当前为{today?.confidence === "high" ? "高" : today?.confidence === "medium" ? "中" : "待完善"}。拍照识别结果和份量经过确认后，结论会更可靠。</span></div>
    </div>
    <div className="confidence-note"><ShieldAlert size={15} />饮食建议不替代医疗诊断或治疗。</div>
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
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    if (!form.age || form.age < 18 || form.age > 64 || !form.height_cm || form.height_cm <= 0 || !form.current_weight_kg || form.current_weight_kg <= 0) {
      setError("请填写有效的年龄、身高和体重");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onConfirm(form);
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
    </div>
    <div className="confidence-note"><Info size={15} />保存后系统会根据新档案重新计算并立即应用每日目标。当前能量目标为 {Math.round(currentGoal?.target.energy_kcal ?? 0)} kcal。</div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    <div className="modal-actions"><button className="button ghost" disabled={saving} onClick={onClose}>取消</button><button className="button primary" disabled={saving} onClick={save}><Check size={16} />{saving ? "保存并计算中…" : "保存并更新目标"}</button></div>
  </section></div>;
}

function ProfilePage({ profile, goal, onEdit }: { profile: Profile; goal: GoalProposal | null; onEdit: () => void }) {
  const [privacyOpen, setPrivacyOpen] = useState(false);
  return <div className="content-stack">
    <div className="page-heading"><span className="eyebrow">我的</span><h1>你的饮食档案</h1><p>目标和建议会随着真实记录变化，但不会未经确认自动修改。</p></div>
    <section className="panel profile-summary"><div className="profile-avatar">{profile.age ?? "食"}</div><div><strong>{profile.age}岁 · {profile.height_cm}cm · {profile.current_weight_kg}kg</strong><span>{profile.primary_goal ? goalLabels[profile.primary_goal] : "未设置目标"} · {profile.activity_level ? activityLabels[profile.activity_level] : "未设置活动水平"}</span></div><button className="icon-button" title="编辑档案" onClick={onEdit}><Pencil size={16} /></button></section>
    <section className="section-block"><div className="section-title"><div><span className="eyebrow">当前目标</span><h2>每日建议</h2></div></div><div className="goal-metrics"><Metric label="能量" value={`${Math.round(goal?.target.energy_kcal ?? 0)} kcal`} /><Metric label="蛋白质" value={`${Math.round(goal?.target.protein_g ?? 0)} g`} /><Metric label="膳食纤维" value={`${Math.round(goal?.target.fiber_g ?? 0)} g`} /></div></section>
    <button className={`privacy-row ${privacyOpen ? "open" : ""}`} onClick={() => setPrivacyOpen((value) => !value)}><ShieldAlert size={17} /><div><strong>你的数据由你控制</strong><span>图片默认保留30天，确认后的结构化记录不会因删除图片而消失。</span>{privacyOpen && <small>本地 V1 数据保存在当前设备的后端存储中；正式上线时应提供导出、删除账号和授权管理入口。</small>}</div><ChevronDown size={16} /></button>
  </div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
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
