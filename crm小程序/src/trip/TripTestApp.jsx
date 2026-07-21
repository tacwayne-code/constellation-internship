import React, { useEffect, useMemo, useState } from "react";
import { Icon } from "../icons";
import {
  calculateDriving,
  deleteExpenseReport,
  geocodeAddress,
  listExpenseReports,
  reverseGeocode,
  reviewExpenseReport,
  submitExpenseReport,
} from "./tripApi";
import "./trip-test.css";

const EMPTY_POINT = { label: "", longitude: "", latitude: "", accuracy: null };
const EMPTY_VISITS = [];
const SAMPLE_POINTS = {
  origin: { label: "广州天河体育中心", longitude: 113.32446, latitude: 23.10647 },
  destination: { label: "深圳南山科技园", longitude: 113.93041, latitude: 22.53332 },
  returnPoint: { label: "广州天河体育中心", longitude: 113.32446, latitude: 23.10647 },
};

function round(value, digits = 1) {
  const factor = 10 ** digits;
  return Math.round((Number(value) + Number.EPSILON) * factor) / factor;
}

function isInChina(longitude, latitude) {
  return longitude >= 72.004 && longitude <= 137.8347 && latitude >= 0.8293 && latitude <= 55.8271;
}

function transformLatitude(x, y) {
  let result = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  result += ((20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2) / 3;
  result += ((20 * Math.sin(y * Math.PI) + 40 * Math.sin((y / 3) * Math.PI)) * 2) / 3;
  result += ((160 * Math.sin((y / 12) * Math.PI) + 320 * Math.sin((y * Math.PI) / 30)) * 2) / 3;
  return result;
}

function transformLongitude(x, y) {
  let result = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  result += ((20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2) / 3;
  result += ((20 * Math.sin(x * Math.PI) + 40 * Math.sin((x / 3) * Math.PI)) * 2) / 3;
  result += ((150 * Math.sin((x / 12) * Math.PI) + 300 * Math.sin((x / 30) * Math.PI)) * 2) / 3;
  return result;
}

function wgs84ToGcj02(longitude, latitude) {
  if (!isInChina(longitude, latitude)) return { longitude, latitude };
  const axis = 6378245;
  const eccentricity = 0.006693421622965943;
  let latitudeDelta = transformLatitude(longitude - 105, latitude - 35);
  let longitudeDelta = transformLongitude(longitude - 105, latitude - 35);
  const latitudeRadians = (latitude / 180) * Math.PI;
  let magic = Math.sin(latitudeRadians);
  magic = 1 - eccentricity * magic * magic;
  const rootMagic = Math.sqrt(magic);
  latitudeDelta = (latitudeDelta * 180) / (((axis * (1 - eccentricity)) / (magic * rootMagic)) * Math.PI);
  longitudeDelta = (longitudeDelta * 180) / ((axis / rootMagic) * Math.cos(latitudeRadians) * Math.PI);
  return { longitude: longitude + longitudeDelta, latitude: latitude + latitudeDelta };
}

function todayKey() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function pointIsValid(point) {
  const longitude = Number(point.longitude);
  const latitude = Number(point.latitude);
  return point.label.trim() && Number.isFinite(longitude) && Number.isFinite(latitude)
    && longitude >= -180 && longitude <= 180 && latitude >= -90 && latitude <= 90;
}

function formatMinutes(minutes) {
  const value = Math.max(0, Number(minutes) || 0);
  const hours = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return hours ? `${hours}小时${rest ? `${rest}分钟` : ""}` : `${rest}分钟`;
}

function PointEditor({ id, title, value, onChange, onLocate, onResolve, resolving, action }) {
  const update = (field, nextValue) => onChange({
    ...value,
    [field]: nextValue,
    ...(field === "label" ? { matchedAddress: "" } : {}),
  });
  return (
    <section className="trip-point" aria-labelledby={`${id}-title`}>
      <div className="trip-point__heading">
        <div>
          <span className="trip-point__index">{id}</span>
          <h2 id={`${id}-title`}>{title}</h2>
        </div>
        {action}
      </div>
      <label className="trip-field">
        <span>地点名称</span>
        <input
          value={value.label}
          onChange={(event) => update("label", event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              onResolve();
            }
          }}
          placeholder={`填写${title}`}
        />
      </label>
      <button className="trip-button trip-button--secondary trip-geocode" type="button" onClick={onResolve} disabled={resolving}>
        <Icon name="search" size={18} />
        {resolving ? "正在查询高德地址" : "按地点名称查坐标"}
      </button>
      {value.matchedAddress ? <p className="trip-point__match">已匹配：{value.matchedAddress}</p> : null}
      <div className="trip-coordinate-row">
        <label className="trip-field">
          <span>经度</span>
          <input
            inputMode="decimal"
            value={value.longitude}
            onChange={(event) => update("longitude", event.target.value)}
            placeholder="113.324460"
          />
        </label>
        <label className="trip-field">
          <span>纬度</span>
          <input
            inputMode="decimal"
            value={value.latitude}
            onChange={(event) => update("latitude", event.target.value)}
            placeholder="23.106470"
          />
        </label>
      </div>
      <button className="trip-button trip-button--secondary trip-locate" type="button" onClick={onLocate}>
        <Icon name="target" size={18} />
        获取当前位置
      </button>
      {value.accuracy ? <p className="trip-point__accuracy">定位精度约 {Math.round(value.accuracy)} 米，已同步更新地点名称</p> : null}
    </section>
  );
}

function RouteLeg({ title, from, to, value, onSelect }) {
  const alternatives = value.alternatives?.length ? value.alternatives : [value];
  return (
    <div className="trip-leg">
      <div className="trip-leg__summary">
        <div>
          <strong>{title}</strong>
          <p>{from.label} → {to.label}</p>
        </div>
        <div className="trip-leg__numbers">
          <strong>{value.distanceKm} km</strong>
          <span>{formatMinutes(value.durationMinutes)} · 高速费 ¥{Number(value.estimatedTollAmount).toFixed(2)}</span>
        </div>
      </div>
      {alternatives.length > 1 ? (
        <div className="trip-route-options" aria-label={`${title}候选路线`}>
          {alternatives.map((candidate, index) => (
            <button
              className={candidate.candidateId === value.candidateId ? "is-selected" : ""}
              key={candidate.candidateId}
              type="button"
              onClick={() => onSelect(candidate.candidateId)}
            >
              <span>路线{index + 1} · {candidate.routeLabel}</span>
              <strong>{candidate.distanceKm}km / ¥{Number(candidate.estimatedTollAmount).toFixed(0)}</strong>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function makeRoute(legs) {
  return {
    legs,
    totalDistanceKm: round(legs.reduce((sum, leg) => sum + leg.distanceKm, 0), 1),
    totalTollAmount: round(legs.reduce((sum, leg) => sum + leg.estimatedTollAmount, 0), 2),
    totalDurationMinutes: legs.reduce((sum, leg) => sum + leg.durationMinutes, 0),
    source: legs.every((leg) => leg.source === "AMAP_LIVE") ? "AMAP_LIVE" : "MOCK_ESTIMATE",
    calculationMode: "FULL_ROUTE",
  };
}

function ReportItem({ report, canReview = false, canDelete = false, note = "", onNoteChange, onReview, onDelete, reviewing }) {
  const fuelAmount = Number(report.actualFuelAmount || 0);
  const tollAmount = Number(report.actualTollAmount || 0);
  const fuelOnly = report.route?.calculationMode === "FUEL_ONLY";
  const destinationLabels = report.destinations?.length
    ? report.destinations.map((point) => point.label)
    : [report.destination?.label].filter(Boolean);
  const routeTitle = fuelOnly
    ? `仅油费报销${report.relatedVisitCount ? ` · 关联 ${report.relatedVisitCount} 条拜访` : ""}`
    : [report.origin?.label, ...destinationLabels, report.returnPoint?.label].filter(Boolean).join(" → ");
  return (
    <article className="trip-report">
      <div className="trip-report__topline">
        <strong>{routeTitle || "行程报销"}</strong>
        <span>{report.submittedTime}</span>
      </div>
      <div className="trip-report__statusline">
        {report.applicantName ? <span>{report.applicantName} · {report.reportDate}</span> : <span>{report.reportDate}</span>}
        <b className={`trip-status trip-status--${String(report.status || "SUBMITTED").toLowerCase()}`}>
          {report.statusLabel || "待经理审批"}
        </b>
      </div>
      <div className="trip-report__totals">
        <span>上报里程 <b>{fuelOnly ? "未计算" : `${report.reportedDistanceKm} km`}</b></span>
        <span>实际油费 <b>¥{fuelAmount.toFixed(2)}</b></span>
        <span>实际高速费 <b>¥{tollAmount.toFixed(2)}</b></span>
        <span>报销合计 <b>¥{(fuelAmount + tollAmount).toFixed(2)}</b></span>
      </div>
      {report.adjustmentReason ? <p>调整说明：{report.adjustmentReason}</p> : null}
      {report.reviewNote ? <p>审批意见：{report.reviewNote}</p> : null}
      {report.reviewerName ? <p>审批人：{report.reviewerName}</p> : null}
      {canReview && report.status === "SUBMITTED" ? (
        <div className="trip-review">
          <label className="trip-field">
            <span>审批意见（驳回时必填）</span>
            <textarea
              rows="2"
              value={note}
              onChange={(event) => onNoteChange(event.target.value)}
              placeholder="可填写报销核对说明"
            />
          </label>
          <div className="trip-review__actions">
            <button type="button" className="trip-button trip-button--secondary" disabled={reviewing} onClick={() => onReview("REJECTED")}>驳回</button>
            <button type="button" className="trip-button trip-button--primary" disabled={reviewing} onClick={() => onReview("APPROVED")}>通过</button>
          </div>
        </div>
      ) : null}
      {canDelete && report.status !== "SUBMITTED" ? (
        <button className="trip-delete-report" type="button" disabled={reviewing} onClick={onDelete}>
          <Icon name="trash" size={16} />
          删除已处理记录
        </button>
      ) : null}
    </article>
  );
}

export default function TripTestApp({ embedded = false, visits = EMPTY_VISITS, user, approvalOnly = false }) {
  const [points, setPoints] = useState(() => ({
    origin: { ...EMPTY_POINT },
    destination: { ...EMPTY_POINT },
    returnPoint: { ...EMPTY_POINT },
  }));
  const [extraDestinations, setExtraDestinations] = useState([]);
  const [route, setRoute] = useState(null);
  const [reportedDistanceKm, setReportedDistanceKm] = useState("");
  const [actualFuelAmount, setActualFuelAmount] = useState("");
  const [actualTollAmount, setActualTollAmount] = useState("");
  const [adjustmentReason, setAdjustmentReason] = useState("");
  const [reports, setReports] = useState([]);
  const [reportsLoading, setReportsLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [reviewing, setReviewing] = useState("");
  const [reviewNotes, setReviewNotes] = useState({});
  const [loading, setLoading] = useState(false);
  const [locating, setLocating] = useState("");
  const [resolving, setResolving] = useState("");
  const [notice, setNotice] = useState({ type: "", text: "" });
  const currentReports = useMemo(() => reports.filter((item) => item.reportDate === todayKey()), [reports]);
  const linkedVisits = useMemo(() => visits.filter((visit) => {
    const visitDate = String(visit.occurredAt || visit.arrivedAt || visit.date || "").slice(0, 10);
    return visitDate === todayKey();
  }), [visits]);

  useEffect(() => {
    let cancelled = false;
    setReportsLoading(true);
    listExpenseReports()
      .then((items) => {
        if (!cancelled) setReports(items);
      })
      .catch((error) => {
        if (!cancelled) setNotice({ type: "error", text: error.message });
      })
      .finally(() => {
        if (!cancelled) setReportsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const updatePoint = (key, value) => {
    if (key.startsWith("extra:")) {
      const id = key.slice(6);
      setExtraDestinations((current) => current.map((point) => (point.id === id ? { ...value, id } : point)));
    } else {
      setPoints((current) => ({ ...current, [key]: value }));
    }
    setRoute(null);
    setNotice({ type: "", text: "" });
  };

  const getPoint = (key) => {
    if (key.startsWith("extra:")) {
      return extraDestinations.find((point) => point.id === key.slice(6));
    }
    return points[key];
  };

  const locate = (key, defaultLabel) => {
    if (!navigator.geolocation) {
      setNotice({ type: "error", text: "当前浏览器不支持定位，请手动填写经纬度。" });
      return;
    }
    setLocating(key);
    setNotice({ type: "", text: "" });
    navigator.geolocation.getCurrentPosition(
      async ({ coords }) => {
        const converted = wgs84ToGcj02(coords.longitude, coords.latitude);
        const locatedPoint = {
          label: defaultLabel,
          longitude: round(converted.longitude, 6),
          latitude: round(converted.latitude, 6),
          accuracy: coords.accuracy,
          matchedAddress: "",
        };
        try {
          const address = await reverseGeocode(locatedPoint);
          updatePoint(key, {
            ...locatedPoint,
            label: address.placeName,
            matchedAddress: address.formattedAddress,
          });
          setNotice({ type: "success", text: `已更新当前位置：${address.placeName}` });
        } catch (error) {
          updatePoint(key, locatedPoint);
          setNotice({ type: "warning", text: `坐标已更新，但地点名称解析失败：${error.message}` });
        }
        setLocating("");
      },
      (error) => {
        const messages = {
          1: "定位权限未开启，请允许浏览器访问位置。",
          2: "暂时无法获取位置，请移动到信号较好的位置。",
          3: "定位超时，请重试或手动填写经纬度。",
        };
        setNotice({ type: "error", text: messages[error.code] || "定位失败，请手动填写经纬度。" });
        setLocating("");
      },
      { enableHighAccuracy: true, timeout: 12_000, maximumAge: 0 },
    );
  };

  const fillSample = () => {
    setPoints({
      origin: { ...SAMPLE_POINTS.origin },
      destination: { ...SAMPLE_POINTS.destination },
      returnPoint: { ...SAMPLE_POINTS.returnPoint },
    });
    setExtraDestinations([]);
    setRoute(null);
    setNotice({ type: "info", text: "已填入广州到深圳的测试路线。" });
  };

  const resolvePointAddress = async (key) => {
    const currentPoint = getPoint(key);
    const address = currentPoint.label.trim();
    if (!address) {
      setNotice({ type: "error", text: "请先填写地点名称或完整地址。" });
      return;
    }
    setResolving(key);
    setNotice({ type: "", text: "" });
    try {
      const result = await geocodeAddress(address);
      updatePoint(key, {
        ...currentPoint,
        longitude: round(result.longitude, 6),
        latitude: round(result.latitude, 6),
        matchedAddress: result.formattedAddress,
        accuracy: null,
      });
      setNotice({ type: "success", text: `已按高德地址更新${currentPoint.label}的经纬度，请核对匹配地址。` });
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setResolving("");
    }
  };

  const calculate = async () => {
    const fuel = Number(actualFuelAmount);
    if (String(actualFuelAmount).trim() === "" || !Number.isFinite(fuel) || fuel < 0) {
      setNotice({ type: "error", text: "请先填写有效的实际油费；没有油费请填 0。" });
      return;
    }
    const destinations = [points.destination, ...extraDestinations];
    const stops = [points.origin, ...destinations, points.returnPoint];
    if (!stops.every(pointIsValid)) {
      const nextRoute = {
        legs: [],
        totalDistanceKm: 0,
        totalTollAmount: 0,
        totalDurationMinutes: 0,
        source: "VISIT_RECORD_ONLY",
        calculationMode: "FUEL_ONLY",
      };
      setRoute(nextRoute);
      setReportedDistanceKm("0");
      setActualTollAmount("0");
      setNotice({
        type: linkedVisits.length ? "success" : "warning",
        text: linkedVisits.length
          ? `路线未填写完整，已关联今日 ${linkedVisits.length} 条拜访记录，按仅油费报销生成。`
          : "路线未填写完整，当前按仅油费报销生成；正式提交前请先保存当天拜访记录。",
      });
      return;
    }
    setLoading(true);
    setNotice({ type: "", text: "" });
    try {
      const legs = await Promise.all(
        stops.slice(0, -1).map((point, index) => calculateDriving(point, stops[index + 1])),
      );
      const nextRoute = makeRoute(legs);
      setRoute(nextRoute);
      setReportedDistanceKm(String(nextRoute.totalDistanceKm));
      setActualTollAmount(String(nextRoute.totalTollAmount));
      setNotice({
        type: nextRoute.source === "AMAP_LIVE" ? "success" : "warning",
        text: nextRoute.source === "AMAP_LIVE"
          ? "高德实时路线计算完成，请按实际行程核对后上报。"
          : "当前未配置高德 Key，展示的是流程模拟数据，高速费暂按 0 元。",
      });
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setLoading(false);
    }
  };

  const selectAlternative = (legIndex, candidateId) => {
    const currentLeg = route.legs[legIndex];
    const candidate = currentLeg.alternatives.find((item) => item.candidateId === candidateId);
    if (!candidate) return;
    const selectedLeg = {
      ...candidate,
      alternatives: currentLeg.alternatives,
      selectionMode: currentLeg.selectionMode,
    };
    const nextRoute = makeRoute(route.legs.map((leg, index) => (index === legIndex ? selectedLeg : leg)));
    setRoute(nextRoute);
    setReportedDistanceKm(String(nextRoute.totalDistanceKm));
    setActualTollAmount(String(nextRoute.totalTollAmount));
    setNotice({ type: "info", text: "已切换候选路线，并更新往返里程和预估高速费。" });
  };

  const submitReport = async () => {
    const distance = Number(reportedDistanceKm);
    const fuel = Number(actualFuelAmount);
    const toll = Number(actualTollAmount);
    if (!route || String(actualFuelAmount).trim() === "" || !Number.isFinite(distance) || distance < 0 || !Number.isFinite(fuel) || fuel < 0 || !Number.isFinite(toll) || toll < 0) {
      setNotice({ type: "error", text: "请填写有效的最终里程、实际油费和实际高速费；没有费用请填 0。" });
      return;
    }
    const now = new Date();
    const report = {
      reportDate: todayKey(),
      submittedAt: now.toISOString(),
      submittedTime: now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
      origin: points.origin,
      destination: points.destination,
      destinations: [points.destination, ...extraDestinations],
      returnPoint: points.returnPoint,
      route,
      reportedDistanceKm: round(distance, 1),
      actualFuelAmount: round(fuel, 2),
      actualTollAmount: round(toll, 2),
      reimbursementTotal: round(fuel + toll, 2),
      adjustmentReason: adjustmentReason.trim(),
      relatedVisitIds: linkedVisits.map((visit) => visit.id),
      relatedVisitCount: linkedVisits.length,
    };
    setSubmitting(true);
    try {
      const saved = await submitExpenseReport(report);
      setReports((current) => [saved, ...current]);
      setNotice({ type: "success", text: "报销申请已提交，经理将在 CRM 待办中收到提醒。" });
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setSubmitting(false);
    }
  };

  const reviewReport = async (report, decision) => {
    const note = String(reviewNotes[report.id] || "").trim();
    if (decision === "REJECTED" && !note) {
      setNotice({ type: "error", text: "驳回报销时请填写原因。" });
      return;
    }
    setReviewing(report.id);
    try {
      const saved = await reviewExpenseReport(report.id, decision, note);
      setReports((current) => current.map((item) => (item.id === saved.id ? saved : item)));
      setNotice({ type: "success", text: decision === "APPROVED" ? "报销已审批通过。" : "报销已驳回并记录原因。" });
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setReviewing("");
    }
  };

  const deleteReport = async (report) => {
    if (!window.confirm("确认删除这条已处理报销记录吗？")) return;
    setReviewing(report.id);
    try {
      await deleteExpenseReport(report.id);
      setReports((current) => current.filter((item) => item.id !== report.id));
      setNotice({ type: "success", text: "报销记录已删除。" });
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setReviewing("");
    }
  };

  const copyOriginToReturn = () => updatePoint("returnPoint", { ...points.origin, label: points.origin.label || "返回出发地" });

  const addDestination = () => {
    if (extraDestinations.length >= 7) {
      setNotice({ type: "warning", text: "单次行程最多添加 8 个客户目的地。" });
      return;
    }
    setExtraDestinations((current) => [
      ...current,
      { ...EMPTY_POINT, id: globalThis.crypto?.randomUUID?.() || `customer-${Date.now()}` },
    ]);
    setRoute(null);
  };

  const removeDestination = (id) => {
    setExtraDestinations((current) => current.filter((point) => point.id !== id));
    setRoute(null);
    setNotice({ type: "", text: "" });
  };

  const destinations = [points.destination, ...extraDestinations];
  const routeStops = [points.origin, ...destinations, points.returnPoint];

  if (approvalOnly) {
    const pending = reports.filter((report) => report.status === "SUBMITTED");
    const reviewed = reports.filter((report) => report.status !== "SUBMITTED");
    return (
      <div className="trip-app trip-app--embedded trip-approval-page">
        <main className="trip-main">
          <section className="trip-intro">
            <div className="trip-step-label">经理审批</div>
            <h2>行程报销待办</h2>
            <p>核对拜访行程、里程、油费和高速费后进行审批。</p>
          </section>
          {notice.text ? <div className={`trip-notice trip-notice--${notice.type}`} role="status">{notice.text}</div> : null}
          <section className="trip-daily">
            <div className="trip-section-heading">
              <div><p className="trip-eyebrow">待处理</p><h2>{pending.length} 条报销申请</h2></div>
            </div>
            {reportsLoading ? <div className="trip-empty"><p>正在读取报销申请...</p></div> : pending.length ? pending.map((report) => (
              <ReportItem
                key={report.id}
                report={report}
                canReview={user?.role === "销售经理"}
                note={reviewNotes[report.id] || ""}
                onNoteChange={(value) => setReviewNotes((current) => ({ ...current, [report.id]: value }))}
                onReview={(decision) => reviewReport(report, decision)}
                reviewing={reviewing === report.id}
              />
            )) : <div className="trip-empty"><Icon name="check" size={25} /><p>当前没有待审批报销</p></div>}
          </section>
          {reviewed.length ? (
            <section className="trip-daily">
              <div className="trip-section-heading"><div><p className="trip-eyebrow">最近记录</p><h2>已审批</h2></div><span>{reviewed.length} 条</span></div>
              {reviewed.map((report) => (
                <ReportItem
                  key={report.id}
                  report={report}
                  canDelete
                  onDelete={() => deleteReport(report)}
                  reviewing={reviewing === report.id}
                />
              ))}
            </section>
          ) : null}
        </main>
      </div>
    );
  }

  return (
    <div className={`trip-app${embedded ? " trip-app--embedded" : ""}`}>
      {!embedded ? (
        <header className="trip-header">
          <div>
            <p>CRM 独立测试模块</p>
            <h1>拜访行程与路费</h1>
          </div>
          <button className="trip-button trip-button--ghost" type="button" onClick={fillSample}>填入测试路线</button>
        </header>
      ) : (
        <div className="trip-embedded-tools">
          <button className="trip-button trip-button--ghost" type="button" onClick={fillSample}>填入测试路线</button>
        </div>
      )}

      <main className="trip-main">
        <section className="trip-intro">
          <div className="trip-step-label">1 / 3</div>
          <h2>设置拜访路线</h2>
          <p>路线可选填；填写完整时计算全程，未填写完整时可结合当天拜访记录生成仅油费报销。</p>
        </section>

        {notice.text ? <div className={`trip-notice trip-notice--${notice.type}`} role="status">{notice.text}</div> : null}

        <div className="trip-points">
          <PointEditor
            id="A"
            title="出发地"
            value={points.origin}
            onChange={(value) => updatePoint("origin", value)}
            onLocate={() => locate("origin", "当前位置（出发）")}
            onResolve={() => resolvePointAddress("origin")}
            resolving={resolving === "origin"}
          />
          <PointEditor
            id="B"
            title="客户目的地 1"
            value={points.destination}
            onChange={(value) => updatePoint("destination", value)}
            onLocate={() => locate("destination", "当前位置（客户）")}
            onResolve={() => resolvePointAddress("destination")}
            resolving={resolving === "destination"}
          />
          {extraDestinations.map((point, index) => {
            const key = `extra:${point.id}`;
            return (
              <PointEditor
                key={point.id}
                id={`B${index + 2}`}
                title={`客户目的地 ${index + 2}`}
                value={point}
                onChange={(value) => updatePoint(key, value)}
                onLocate={() => locate(key, `当前位置（客户 ${index + 2}）`)}
                onResolve={() => resolvePointAddress(key)}
                resolving={resolving === key}
                action={(
                  <button className="trip-link-button trip-link-button--danger" type="button" onClick={() => removeDestination(point.id)}>
                    <Icon name="trash" size={16} />
                    删除
                  </button>
                )}
              />
            );
          })}
          <button className="trip-button trip-button--secondary trip-add-destination" type="button" onClick={addDestination}>
            <Icon name="plus" size={18} />
            增加客户目的地
          </button>
          <PointEditor
            id="R"
            title="返程地"
            value={points.returnPoint}
            onChange={(value) => updatePoint("returnPoint", value)}
            onLocate={() => locate("returnPoint", "当前位置（返程）")}
            onResolve={() => resolvePointAddress("returnPoint")}
            resolving={resolving === "returnPoint"}
            action={(
              <button className="trip-link-button" type="button" onClick={copyOriginToReturn}>
                <Icon name="refresh" size={16} />
                同出发地
              </button>
            )}
          />
        </div>

        <section className="trip-fuel-entry" aria-labelledby="trip-fuel-title">
          <div className="trip-fuel-entry__icon" aria-hidden="true">¥</div>
          <div className="trip-fuel-entry__content">
            <h2 id="trip-fuel-title">油费报销</h2>
            <p>按实际加油金额填写；没有油费填 0。</p>
            <label className="trip-field">
              <span>实际油费（元）</span>
              <input inputMode="decimal" value={actualFuelAmount} onChange={(event) => setActualFuelAmount(event.target.value)} placeholder="销售手动填写" />
            </label>
            {embedded ? (
              <small className={linkedVisits.length ? "trip-visit-link trip-visit-link--ok" : "trip-visit-link"}>
                {linkedVisits.length ? `已关联今日 ${linkedVisits.length} 条拜访记录` : "当前未发现今日拜访记录，正式提交前请先保存拜访记录"}
              </small>
            ) : null}
          </div>
        </section>

        <button className="trip-button trip-button--primary trip-calculate" type="button" onClick={calculate} disabled={loading || locating || resolving}>
          <Icon name={loading ? "refresh" : "pin"} size={19} className={loading ? "trip-spin" : ""} />
          {loading ? "正在计算报销金额" : locating ? "正在获取位置和地址" : "计算行程与报销金额"}
        </button>

        {route ? (
          <section className="trip-result" aria-labelledby="trip-result-title">
            <div className="trip-section-heading">
              <div>
                <div className="trip-step-label">2 / 3</div>
                <h2 id="trip-result-title">{route.calculationMode === "FUEL_ONLY" ? "报销核对" : "路线预估"}</h2>
              </div>
              <span className={`trip-source trip-source--${route.source === "AMAP_LIVE" ? "live" : route.source === "VISIT_RECORD_ONLY" ? "visit" : "mock"}`}>
                {route.source === "AMAP_LIVE" ? "高德实时" : route.source === "VISIT_RECORD_ONLY" ? "仅油费" : "模拟估算"}
              </span>
            </div>
            <div className="trip-legs">
              {route.legs.length ? route.legs.map((leg, index) => (
                <RouteLeg
                  key={`${index}-${routeStops[index].label}-${routeStops[index + 1].label}`}
                  title={index === route.legs.length - 1 ? "返程" : `拜访第 ${index + 1} 段`}
                  from={routeStops[index]}
                  to={routeStops[index + 1]}
                  value={leg}
                  onSelect={(id) => selectAlternative(index, id)}
                />
              )) : (
                <div className="trip-route-skipped">
                  <Icon name="order" size={24} />
                  <div>
                    <strong>本次未计算路线</strong>
                    <p>根据当天拜访记录提交仅油费报销，里程和高速费按 0 记录。</p>
                  </div>
                </div>
              )}
            </div>
            <div className="trip-summary">
              <div><span>全程里程</span><strong>{route.calculationMode === "FUEL_ONLY" ? "未计算" : `${route.totalDistanceKm} km`}</strong></div>
              <div><span>预计用时</span><strong>{route.calculationMode === "FUEL_ONLY" ? "未计算" : formatMinutes(route.totalDurationMinutes)}</strong></div>
              <div><span>预估高速费</span><strong>¥{route.totalTollAmount.toFixed(2)}</strong></div>
            </div>

            <div className="trip-expense-total">
              <span>本次报销预计合计</span>
              <strong>¥{(Number(actualFuelAmount || 0) + Number(actualTollAmount || 0)).toFixed(2)}</strong>
              <small>油费 ¥{Number(actualFuelAmount || 0).toFixed(2)} + 高速费 ¥{Number(actualTollAmount || 0).toFixed(2)}</small>
            </div>

            <div className="trip-confirm">
              <div className="trip-step-label">3 / 3</div>
              <h2>核对并上报</h2>
              <p>{route.calculationMode === "FUEL_ONLY" ? "本次按仅油费报销生成；如有实际里程或高速费，可在下方补充。" : "定位、路线、ETC 和临时绕行会造成差异，以下两项可按实际修改。"}</p>
              <div className="trip-edit-row">
                <label className="trip-field">
                  <span>最终里程（km）</span>
                  <input inputMode="decimal" value={reportedDistanceKm} onChange={(event) => setReportedDistanceKm(event.target.value)} />
                </label>
                <label className="trip-field">
                  <span>实际高速费（元，未走高速填 0）</span>
                  <input inputMode="decimal" value={actualTollAmount} onChange={(event) => setActualTollAmount(event.target.value)} />
                </label>
              </div>
              <label className="trip-field">
                <span>调整说明（选填）</span>
                <textarea value={adjustmentReason} onChange={(event) => setAdjustmentReason(event.target.value)} placeholder="例如：加油票据、临时绕行、ETC 优惠、实际停车点变化" rows="3" />
              </label>
              <button className="trip-button trip-button--primary" type="button" onClick={submitReport} disabled={submitting}>
                <Icon name="check" size={19} />
                {submitting ? "正在提交报销申请" : "确认上报并提交审批"}
              </button>
            </div>
          </section>
        ) : null}

        <section className="trip-daily" aria-labelledby="trip-daily-title">
          <div className="trip-section-heading">
            <div>
              <p className="trip-eyebrow">{todayKey()}</p>
              <h2 id="trip-daily-title">当日行程单</h2>
            </div>
            <span>{currentReports.length} 条</span>
          </div>
          {reportsLoading ? <div className="trip-empty"><p>正在读取行程单...</p></div> : currentReports.length ? currentReports.map((report) => (
            <ReportItem
              key={report.id}
              report={report}
              canDelete
              onDelete={() => deleteReport(report)}
              reviewing={reviewing === report.id}
            />
          )) : (
            <div className="trip-empty">
              <Icon name="order" size={25} />
              <p>今天还没有已上报的行程</p>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
