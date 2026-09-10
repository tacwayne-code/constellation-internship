import React, { useEffect, useRef, useState } from "react";
import { authHeaders } from "../auth/session.js";
import { reverseGeocode } from "../trip/tripApi.js";
import { wgs84ToGcj02 } from "./coordinates.js";

export default function AddressAssist({ companyName = "", value, onChange }) {
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [matches, setMatches] = useState(null);
  const latest = useRef({ companyName, value });
  const sequence = useRef(0);
  latest.current = { companyName, value };
  useEffect(() => () => { sequence.current += 1; }, []);

  const run = async (kind) => {
    const ticket = ++sequence.current;
    const original = { companyName, value };
    const current = () => ticket === sequence.current;
    setNotice("");
    setMatches(null);
    setBusy(kind);
    try {
      if (kind === "search") {
        if (!companyName.trim()) throw new Error("请先填写客户公司名称");
        const response = await fetch("/api/locations/search", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", ...authHeaders("POST") },
          body: JSON.stringify({ keyword: companyName.trim() }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.message || "地址查询失败，请手动填写");
        if (!current() || latest.current.companyName !== original.companyName) return;
        setMatches({ query: companyName, items: payload.items || [] });
        setNotice(payload.items?.length ? "请选择实际办公或拜访地点，选中后自动填入地址。地图地址不等同于工商注册地址。" : "没有找到匹配企业，可补全企业名称或手动填写地址");
      } else {
        if (!window.isSecureContext) throw new Error("当前局域网HTTP页面不支持手机定位；请在正式HTTPS页面使用，或按企业名查地址");
        if (!navigator.geolocation) throw new Error("当前浏览器不支持定位，请按企业名查地址或手动填写");
        const position = await new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve,
          () => reject(new Error("未能获取位置，请检查定位权限后重试，或手动填写地址")),
          { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }));
        const point = wgs84ToGcj02(position.coords.longitude, position.coords.latitude);
        const result = await reverseGeocode(point);
        if (!current()) return;
        if (latest.current.value !== original.value || latest.current.companyName !== original.companyName) {
          setNotice("你已修改客户或地址，已保留手动填写内容");
          return;
        }
        if (!result.formattedAddress) throw new Error("未能解析详细地址，请手动填写");
        onChange(result.formattedAddress);
        setNotice(`已填入当前位置${result.placeName ? `：${result.placeName}` : ""}。请核对门牌、楼层；只有到达客户现场时才适用。`);
      }
    } catch (error) {
      if (current()) setNotice(error.message || "地址获取失败，请手动填写");
    } finally {
      if (current()) setBusy("");
    }
  };

  return <div className="address-assist">
    <div className="address-assist__actions">
      <button className="secondary" type="button" disabled={Boolean(busy)} onClick={() => run("locate")}>
        {busy === "locate" ? "正在获取地址…" : "获取当前位置"}
      </button>
      <button className="secondary" type="button" disabled={Boolean(busy)} onClick={() => run("search")}>
        {busy === "search" ? "正在查询…" : "按企业名查地址"}
      </button>
    </div>
    {notice && <p role="status">{notice}</p>}
    {matches?.query === companyName && matches.items.map((item) => <button
      className="address-assist__match" type="button" key={item.id || `${item.name}-${item.formattedAddress}`}
      onClick={() => { onChange(item.formattedAddress); setMatches(null); setNotice("已填入所选地址，请核对门牌和楼层后保存"); }}>
      <strong>{item.name}</strong><span>{item.formattedAddress}</span>
    </button>)}
  </div>;
}
