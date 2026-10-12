"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError, type BalanceScript, apiRequest } from "@/lib/api";
import { titleForLocale, useI18n } from "@/lib/i18n";

const PythonEditor = dynamic(
  async () => {
    const [{ default: CodeMirror }, { python }] = await Promise.all([
      import("@uiw/react-codemirror"),
      import("@codemirror/lang-python"),
    ]);
    return function Editor({
      value,
      onChange,
    }: {
      value: string;
      onChange: (value: string) => void;
    }) {
      return (
        <CodeMirror
          value={value}
          height="280px"
          theme="light"
          extensions={[python()]}
          basicSetup={{ autocompletion: false, lintKeymap: false }}
          onChange={onChange}
          className="overflow-hidden rounded-md border bg-background text-xs [&_.cm-editor]:bg-background [&_.cm-gutters]:bg-muted"
        />
      );
    };
  },
  { ssr: false },
);

const EMPTY_CODE = `import json, sys
payload = json.load(sys.stdin)
print(json.dumps({"total": "0", "balance": "0", "currency": "USD"}))
`;

type Draft = {
  name: string;
  timeout: string;
  code: string;
};

type TestResult = {
  ok: boolean;
  total: string;
  balance: string;
  currency: string;
  error_message: string;
};

function emptyDraft(): Draft {
  return { name: "", timeout: "10", code: EMPTY_CODE };
}

export function BalanceScriptsPanel() {
  const { locale } = useI18n();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [testKey, setTestKey] = useState("");
  const [testUrl, setTestUrl] = useState("");
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const scriptsQuery = useQuery({
    queryKey: ["balance-scripts"],
    queryFn: () => apiRequest<BalanceScript[]>("/admin/balance-scripts"),
  });
  const scripts = scriptsQuery.data ?? [];

  function selectScript(script: BalanceScript) {
    setSelectedId(script.id);
    setDraft({
      name: script.name,
      timeout: String(script.timeout_seconds),
      code: script.code,
    });
    setTestResult(null);
  }

  async function saveScript() {
    const timeout = Number(draft.timeout);
    const payload = {
      name: draft.name.trim(),
      code: draft.code,
      timeout_seconds: Number.isFinite(timeout) ? timeout : 10,
    };
    setSaving(true);
    try {
      const saved = selectedId
        ? await apiRequest<BalanceScript>(
            `/admin/balance-scripts/${selectedId}`,
            {
              method: "PUT",
              body: JSON.stringify(payload),
            },
          )
        : await apiRequest<BalanceScript>("/admin/balance-scripts", {
            method: "POST",
            body: JSON.stringify(payload),
          });
      setSelectedId(saved.id);
      await queryClient.invalidateQueries({ queryKey: ["balance-scripts"] });
      toast.success(titleForLocale(locale, "已保存", "Saved"));
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : titleForLocale(locale, "保存失败", "Save failed");
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  async function deleteScript() {
    if (!selectedId) return;
    setSaving(true);
    try {
      await apiRequest<void>(`/admin/balance-scripts/${selectedId}`, {
        method: "DELETE",
      });
      setSelectedId(null);
      setDraft(emptyDraft());
      await queryClient.invalidateQueries({ queryKey: ["balance-scripts"] });
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : titleForLocale(locale, "删除失败", "Delete failed");
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  async function testScript() {
    const timeout = Number(draft.timeout);
    setTesting(true);
    try {
      const result = await apiRequest<TestResult>(
        "/admin/balance-scripts/test",
        {
          method: "POST",
          body: JSON.stringify({
            code: draft.code,
            timeout_seconds: Number.isFinite(timeout) ? timeout : 10,
            api_key: testKey,
            base_url: testUrl,
            proxy: "",
          }),
        },
      );
      setTestResult(result);
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : titleForLocale(locale, "试跑失败", "Test failed");
      toast.error(message);
    } finally {
      setTesting(false);
    }
  }

  return (
    <section className="min-w-0 rounded-2xl border bg-card px-4 py-4 shadow-sm sm:px-6 sm:py-5">
      <header className="flex items-center justify-between gap-3 border-b pb-4">
        <h2 className="text-base font-semibold text-foreground">
          {titleForLocale(locale, "余额查询脚本", "Balance scripts")}
        </h2>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            setSelectedId(null);
            setDraft(emptyDraft());
            setTestResult(null);
          }}
        >
          <Plus data-icon="inline-start" />
          {titleForLocale(locale, "新建", "New")}
        </Button>
      </header>
      <div className="grid gap-5 pt-5 lg:grid-cols-[200px_minmax(0,1fr)]">
        <div className="space-y-1">
          {scripts.map((script) => (
            <button
              key={script.id}
              type="button"
              className={
                script.id === selectedId
                  ? "w-full rounded-md border border-primary/30 bg-primary/10 px-2.5 py-2 text-left text-sm font-medium"
                  : "w-full rounded-md border px-2.5 py-2 text-left text-sm hover:bg-muted"
              }
              onClick={() => selectScript(script)}
            >
              {script.name}
            </button>
          ))}
        </div>
        <FieldGroup>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel>{titleForLocale(locale, "名称", "Name")}</FieldLabel>
              <Input
                value={draft.name}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    name: event.target.value,
                  }))
                }
              />
            </Field>
            <Field>
              <FieldLabel>
                {titleForLocale(locale, "超时（秒）", "Timeout (s)")}
              </FieldLabel>
              <Input
                type="number"
                min="1"
                max="15"
                step="1"
                value={draft.timeout}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    timeout: event.target.value,
                  }))
                }
              />
            </Field>
          </div>
          <Field>
            <FieldLabel>Python</FieldLabel>
            <PythonEditor
              value={draft.code}
              onChange={(code) => setDraft((current) => ({ ...current, code }))}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel>API Key</FieldLabel>
              <Input
                value={testKey}
                onChange={(event) => setTestKey(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel>Base URL</FieldLabel>
              <Input
                value={testUrl}
                onChange={(event) => setTestUrl(event.target.value)}
              />
            </Field>
          </div>
          {testResult ? (
            <div className="font-mono text-xs text-muted-foreground">
              {testResult.ok
                ? `${testResult.balance} / ${testResult.total} ${testResult.currency}`
                : testResult.error_message}
            </div>
          ) : null}
          <div className="flex items-center gap-2">
            <Button
              type="button"
              disabled={saving}
              onClick={() => void saveScript()}
            >
              {titleForLocale(locale, "保存脚本", "Save script")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={testing}
              onClick={() => void testScript()}
            >
              {titleForLocale(locale, "试跑", "Test")}
            </Button>
            {selectedId ? (
              <Button
                type="button"
                variant="outline"
                disabled={saving}
                onClick={() => void deleteScript()}
              >
                <Trash2 data-icon="inline-start" />
                {titleForLocale(locale, "删除", "Delete")}
              </Button>
            ) : null}
          </div>
        </FieldGroup>
      </div>
    </section>
  );
}
