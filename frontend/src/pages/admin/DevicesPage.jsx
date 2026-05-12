import React, { useEffect, useState } from "react";
import { Search, Cpu, ChevronRight } from "lucide-react";
import { api } from "../../lib/api";
import { Card } from "../../components/ui/card";
import { Input } from "../../components/ui/input";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle
} from "../../components/ui/dialog";
import { StatusBadge, formatDate } from "../../lib/status";

export default function DevicesPage() {
  const [q, setQ] = useState("");
  const [list, setList] = useState([]);
  const [selected, setSelected] = useState(null);
  const [history, setHistory] = useState(null);

  const load = async () => {
    const { data } = await api.get(`/devices${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    setList(data);
  };

  useEffect(() => {
    const t = setTimeout(load, 200);
    return () => clearTimeout(t);
  }, [q]);

  const openDevice = async (d) => {
    setSelected(d);
    const { data } = await api.get(`/devices/${d.device_id}`);
    setHistory(data);
  };

  return (
    <div className="space-y-6" data-testid="devices-page">
      <div>
        <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Inventory</div>
        <h1 className="font-display font-black text-3xl tracking-tight text-navy">Devices</h1>
        <p className="text-slate-500 text-sm mt-1">Search by Device ID, serial number, brand or model.</p>
      </div>

      <div className="relative max-w-lg">
        <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="DEV-2026-0001 or serial number…"
               className="pl-9 h-11"
               data-testid="device-search-input" />
      </div>

      <Card className="rounded-md overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr className="text-left text-[10px] uppercase tracking-[0.15em] text-slate-500">
              <th className="p-3 font-bold">Device ID</th>
              <th className="p-3 font-bold">Brand / Model</th>
              <th className="p-3 font-bold">Serial</th>
              <th className="p-3 font-bold">Warranty</th>
              <th className="p-3 font-bold">Added</th>
              <th className="p-3"></th>
            </tr>
          </thead>
          <tbody>
            {list.map((d) => (
              <tr key={d.device_id} className="border-b border-slate-100 hover:bg-slate-50 cursor-pointer"
                  onClick={() => openDevice(d)} data-testid={`device-row-${d.device_id}`}>
                <td className="p-3 font-mono font-bold text-signal">{d.device_id}</td>
                <td className="p-3">
                  <div className="font-semibold text-navy">{d.brand} {d.model}</div>
                </td>
                <td className="p-3 font-mono text-xs text-slate-500">{d.serial_number || "—"}</td>
                <td className="p-3">
                  <span className={`text-[10px] uppercase font-bold px-1.5 py-0.5 rounded ${
                    d.warranty_status === "active" ? "bg-emerald-50 text-emerald-700" :
                    d.warranty_status === "expired" ? "bg-amber-50 text-amber-700" :
                    "bg-slate-100 text-slate-600"
                  }`}>
                    {d.warranty_status}
                  </span>
                  {d.warranty_expiry && (
                    <div className="text-[10px] text-slate-500 mt-0.5">until {d.warranty_expiry}</div>
                  )}
                </td>
                <td className="p-3 text-xs text-slate-500">{formatDate(d.created_at)}</td>
                <td className="p-3"><ChevronRight className="w-4 h-4 text-slate-400" /></td>
              </tr>
            ))}
            {list.length === 0 && (
              <tr><td colSpan={6} className="p-8 text-center text-slate-500">No devices found</td></tr>
            )}
          </tbody>
        </table>
      </Card>

      <Dialog open={!!selected} onOpenChange={(v) => !v && (setSelected(null), setHistory(null))}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Cpu className="w-5 h-5 text-navy" />
              <span className="font-mono">{selected?.device_id}</span>
            </DialogTitle>
          </DialogHeader>
          {history?.device && (
            <div className="grid grid-cols-2 gap-3 text-sm mb-4 p-3 bg-slate-50 rounded">
              <div><span className="text-xs text-slate-500">Brand/Model:</span> <b>{history.device.brand} {history.device.model}</b></div>
              <div><span className="text-xs text-slate-500">Serial:</span> <span className="font-mono">{history.device.serial_number || "—"}</span></div>
              <div><span className="text-xs text-slate-500">Warranty:</span> <b>{history.device.warranty_status}</b></div>
              <div><span className="text-xs text-slate-500">Expires:</span> {history.device.warranty_expiry || "—"}</div>
            </div>
          )}
          <div>
            <div className="text-xs uppercase tracking-wider font-bold text-slate-500 mb-2">
              Service history ({history?.history?.length || 0})
            </div>
            <div className="max-h-80 overflow-auto space-y-2">
              {history?.history?.map((h) => (
                <div key={h.id} className={`p-3 rounded border-l-4 bg-slate-50 border-status-${h.status}`}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-mono font-bold text-signal text-sm">{h.ticket_number}</span>
                    <StatusBadge status={h.status} />
                  </div>
                  <div className="text-sm text-slate-700 truncate">{h.problem_description}</div>
                  <div className="text-xs text-slate-500 mt-1">
                    {h.engineer_name ? `by ${h.engineer_name} • ` : ""}{formatDate(h.created_at)}
                  </div>
                </div>
              ))}
              {(!history?.history || history.history.length === 0) && (
                <div className="text-sm text-slate-500 text-center py-8">No service history yet</div>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
