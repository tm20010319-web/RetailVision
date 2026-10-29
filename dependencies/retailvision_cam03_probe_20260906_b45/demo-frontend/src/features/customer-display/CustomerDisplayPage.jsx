import { Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { AmbientBackground } from "./components/AmbientBackground";
import { IdleExperience } from "./components/IdleExperience";
import { MemberWelcomeExperience } from "./components/MemberWelcomeExperience";
import { memberDisplayData, PrivacyMode, toMemberDisplayData } from "./data/memberDisplayData";
import { useMemberArrivalController } from "./memberArrivalController";
import "./styles/customer-display.css";

export function CustomerDisplayPage({ data = memberDisplayData, privacyMode = data.privacyMode || PrivacyMode.FULL }) {
  const reducedMotion = useRef(false);
  const [manualMode, setManualMode] = useState(false);
  const {
    complete: completeArrival,
    currentMember,
    triggerMock,
  } = useMemberArrivalController({
    getRecognitions: api.getRecognitions,
    getMember: api.getMember,
    mockData: data,
  });
  const activeData = currentMember
    ? toMemberDisplayData(currentMember, data)
    : data;
  const isWelcoming = Boolean(currentMember) || manualMode;

  const startWelcome = useCallback(() => {
    if (!isWelcoming) {
      setManualMode(true);
      triggerMock();
    }
  }, [isWelcoming, triggerMock]);

  const completeWelcome = useCallback(() => {
    setManualMode(false);
    completeArrival();
  }, [completeArrival]);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "秒象科技 · 会员欢迎大屏";
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    reducedMotion.current = media.matches;
    return () => {
      document.title = previousTitle;
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event) {
      if (event.key.toLowerCase() === "m" && !event.repeat) {
        startWelcome();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [startWelcome]);

  return (
    <main
      className={`customer-display-shell${isWelcoming ? " customer-display-shell--welcoming" : ""}`}
      data-testid="customer-display"
    >
      <AmbientBackground active={isWelcoming} />

      <div className="display-chrome display-chrome--brand" aria-hidden="true">
        <span className="display-chrome__mark">M</span>
        <span>CUSTOMER WELCOME SPACE</span>
      </div>
      <div className="display-chrome display-chrome--status" aria-hidden="true">
        <i />
        <span>WELCOME SYSTEM READY</span>
      </div>

      <IdleExperience hidden={isWelcoming} />
      <MemberWelcomeExperience
        active={isWelcoming}
        data={activeData}
        onComplete={completeWelcome}
        privacyMode={privacyMode}
        reducedMotion={reducedMotion.current}
      />

      <button
        className="demo-trigger"
        disabled={isWelcoming}
        onClick={startWelcome}
        type="button"
      >
        <Sparkles aria-hidden="true" size={17} strokeWidth={1.7} />
        <span>{isWelcoming ? "欢迎仪式进行中" : "模拟会员到店"}</span>
        <kbd>M</kbd>
      </button>

      <p className="sr-only" aria-live="polite">
        {isWelcoming ? `欢迎回来，${privacyMode === PrivacyMode.EMOTIONAL_ONLY ? "尊贵会员" : activeData.displayName}。${activeData.serviceCopy}` : "欢迎来到秒象科技。"}
      </p>
    </main>
  );
}
