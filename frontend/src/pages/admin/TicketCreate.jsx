import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api, formatError } from "../../lib/api";
import { Card } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue
} from "../../components/ui/select";
import { ArrowLeft, Save, Loader2 } from "lucide-react";

export default function TicketCreate() {
  const nav = useNavigate();
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    customer_name: "",
    customer_phone: "",
    customer_company: "",
    contact_source: "call",
    problem_description: "",
    device: {
      brand: "",
      model: "",
      serial_number: "",
      warranty_status: "none",
      warranty_expiry: "",
    },
  });

  const setF = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const setD = (k, v) => setForm((f) => ({ ...f, device: { ...f.device, [k]: v } }));

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      const payload = {
        ...form,
        device: {
          ...form.device,
          serial_number: form.device.serial_number || null,
          warranty_expiry: form.device.warranty_expiry || null,
        },
      };
      const { data } = await api.post("/tickets", payload);
      toast.success(`Ticket ${data.ticket_number} created`);
      nav(`/admin/tickets/${data.id}`);
    } catch (err) {
      toast.error(formatError(err.response?.data?.detail) || "Failed");
    } finally { setSaving(false); }
  };

  return (
    <div className="max-w-3xl space-y-6" data-testid="ticket-create-page">
      <button
        onClick={() => nav(-1)}
        className="inline-flex items-center gap-1 text-xs font-bold text-slate-500 hover:text-navy uppercase tracking-wider"
      >
        <ArrowLeft className="w-4 h-4" /> Back
      </button>
      <div>
        <div className="text-xs uppercase tracking-[0.2em] text-slate-500">New ticket</div>
        <h1 className="font-display font-black text-3xl tracking-tight text-navy">Create service ticket</h1>
      </div>

      <form onSubmit={submit} className="space-y-6">
        <Card className="p-6 rounded-md">
          <h3 className="font-bold text-navy mb-4 text-sm uppercase tracking-wider">Customer</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <Label className="text-xs font-bold">Customer name *</Label>
              <Input value={form.customer_name} onChange={(e) => setF("customer_name", e.target.value)} required
                     data-testid="customer-name-input" className="mt-1.5" />
            </div>
            <div>
              <Label className="text-xs font-bold">Phone *</Label>
              <Input value={form.customer_phone} onChange={(e) => setF("customer_phone", e.target.value)} required
                     data-testid="customer-phone-input" className="mt-1.5" />
            </div>
            <div>
              <Label className="text-xs font-bold">Company</Label>
              <Input value={form.customer_company} onChange={(e) => setF("customer_company", e.target.value)}
                     className="mt-1.5" />
            </div>
            <div>
              <Label className="text-xs font-bold">Contact source</Label>
              <Select value={form.contact_source} onValueChange={(v) => setF("contact_source", v)}>
                <SelectTrigger className="mt-1.5"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="call">Call</SelectItem>
                  <SelectItem value="whatsapp">WhatsApp</SelectItem>
                  <SelectItem value="email">Email</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </Card>

        <Card className="p-6 rounded-md">
          <h3 className="font-bold text-navy mb-4 text-sm uppercase tracking-wider">Device</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <Label className="text-xs font-bold">Brand *</Label>
              <Input value={form.device.brand} onChange={(e) => setD("brand", e.target.value)} required
                     placeholder="Dell / HP / Apple…"
                     data-testid="device-brand-input" className="mt-1.5" />
            </div>
            <div>
              <Label className="text-xs font-bold">Model *</Label>
              <Input value={form.device.model} onChange={(e) => setD("model", e.target.value)} required
                     placeholder="Latitude 5420"
                     data-testid="device-model-input" className="mt-1.5" />
            </div>
            <div>
              <Label className="text-xs font-bold">Serial number</Label>
              <Input value={form.device.serial_number} onChange={(e) => setD("serial_number", e.target.value)}
                     placeholder="Leave blank to auto-generate Device ID"
                     data-testid="device-serial-input" className="mt-1.5 font-mono" />
            </div>
            <div>
              <Label className="text-xs font-bold">Warranty status</Label>
              <Select value={form.device.warranty_status} onValueChange={(v) => setD("warranty_status", v)}>
                <SelectTrigger className="mt-1.5"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">Active</SelectItem>
                  <SelectItem value="expired">Expired</SelectItem>
                  <SelectItem value="none">None</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {form.device.warranty_status === "active" && (
              <div className="sm:col-span-2">
                <Label className="text-xs font-bold">Warranty expiry</Label>
                <Input type="date" value={form.device.warranty_expiry}
                       onChange={(e) => setD("warranty_expiry", e.target.value)}
                       className="mt-1.5" />
              </div>
            )}
          </div>
        </Card>

        <Card className="p-6 rounded-md">
          <h3 className="font-bold text-navy mb-4 text-sm uppercase tracking-wider">Problem</h3>
          <Textarea
            value={form.problem_description}
            onChange={(e) => setF("problem_description", e.target.value)}
            placeholder="Describe the issue reported by the customer…"
            rows={5}
            required
            data-testid="problem-description-input"
          />
        </Card>

        <div className="flex gap-3 justify-end">
          <Button type="button" variant="outline" onClick={() => nav(-1)}>Cancel</Button>
          <Button type="submit" disabled={saving}
                  className="bg-navy hover:bg-navy/90 text-white font-bold rounded-md"
                  data-testid="ticket-create-submit-btn">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <><Save className="w-4 h-4 mr-2" /> Create ticket</>}
          </Button>
        </div>
      </form>
    </div>
  );
}
