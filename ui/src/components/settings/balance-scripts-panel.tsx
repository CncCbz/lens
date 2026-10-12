"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Settings, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Dialog, AppDialogContent } from "@/components/ui/dialog";
import {
  ApiError,
  type BalanceScript,
  type BalanceScriptParameter,
  apiRequest,
} from "@/lib/api";
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
          height="100%"
          theme="light"
          extensions={[python()]}
          basicSetup={{ autocompletion: false, lintKeymap: false }}
          onChange={onChange}
          className="h-full overflow-hidden rounded-md border bg-background text-xs [&_.cm-editor]:h-full [&_.cm-editor]:bg-background [&_.cm-gutters]:bg-muted [&_.cm-scroller]:h-full"
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
  parameters: BalanceScriptParameter[];
};

type TestResult = {
  ok: boolean;
  total: string;
  balance: string;
  currency: string;
  error_message: string;
};

function emptyDraft(): Draft {
  return { name: "", timeout: "10", code: EMPTY_CODE, parameters: [] };
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
  const [testParams, setTestParams] = useState<Record<string, string>>({});
  const [testParamsOpen, setTestParamsOpen] = useState(false);

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
      parameters: script.parameters ?? [],
    });
    setTestResult(null);
  }

  async function saveScript() {
    const timeout = Number(draft.timeout);
    const payload = {
      name: draft.name.trim(),
      code: draft.code,
      timeout_seconds: Number.isFinite(timeout) ? timeout : 10,
      parameters: draft.parameters.filter(
        (item) => item.key.trim() && item.label.trim(),
      ),
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
            params: testParams,
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
    <>
      <section className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-2xl border bg-card shadow-sm">
        <header className="flex shrink-0 items-center justify-between gap-3 border-b px-4 py-4 sm:px-6">
          <h2 className="text-base font-semibold text-foreground">
            {titleForLocale(locale, "余额查询脚本", "Balance scripts")}
          </h2>
          <div className="flex items-center gap-2">
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
            <Button
              type="button"
              size="sm"
              disabled={saving}
              onClick={() => void saveScript()}
            >
              {titleForLocale(locale, "保存", "Save")}
            </Button>
            {selectedId ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={saving}
                onClick={() => void deleteScript()}
              >
                <Trash2 data-icon="inline-start" />
                {titleForLocale(locale, "删除", "Delete")}
              </Button>
            ) : null}
          </div>
        </header>
        <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(12rem,1fr)] lg:grid-cols-[220px_minmax(0,1fr)] lg:grid-rows-1">
          <div className="hide-scrollbar max-h-48 min-h-0 overflow-y-auto border-b p-2 lg:max-h-none lg:border-r lg:border-b-0">
            {scripts.map((script) => (
              <button
                key={script.id}
                type="button"
                className={
                  script.id === selectedId
                    ? "flex h-9 w-full items-center truncate rounded-md bg-sidebar-accent px-3 text-left text-sm font-medium"
                    : "flex h-9 w-full items-center truncate rounded-md px-3 text-left text-sm hover:bg-muted"
                }
                onClick={() => selectScript(script)}
              >
                {script.name}
              </button>
            ))}
          </div>
          <div className="flex h-full min-h-0 min-w-0 flex-col">
            <FieldGroup className="shrink-0 gap-4 px-4 py-4 sm:px-6">
              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_9rem]">
                <Field>
                  <FieldLabel>
                    {titleForLocale(locale, "名称", "Name")}
                  </FieldLabel>
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
              <div className="grid gap-2">
                {draft.parameters.map((parameter, index) => (
                  <div
                    key={index}
                    className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_2rem] gap-2"
                  >
                    <Input
                      value={parameter.key}
                      placeholder="key"
                      onChange={(event) =>
                        setDraft((current) => ({
                          ...current,
                          parameters: current.parameters.map(
                            (item, itemIndex) =>
                              itemIndex === index
                                ? { ...item, key: event.target.value }
                                : item,
                          ),
                        }))
                      }
                    />
                    <Input
                      value={parameter.label}
                      placeholder={titleForLocale(locale, "名称", "Label")}
                      onChange={(event) =>
                        setDraft((current) => ({
                          ...current,
                          parameters: current.parameters.map(
                            (item, itemIndex) =>
                              itemIndex === index
                                ? { ...item, label: event.target.value }
                                : item,
                          ),
                        }))
                      }
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      onClick={() =>
                        setDraft((current) => ({
                          ...current,
                          parameters: current.parameters.filter(
                            (_, itemIndex) => itemIndex !== index,
                          ),
                        }))
                      }
                    >
                      <X size={16} />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-fit"
                  onClick={() =>
                    setDraft((current) => ({
                      ...current,
                      parameters: [
                        ...current.parameters,
                        { key: "", label: "" },
                      ],
                    }))
                  }
                >
                  <Plus data-icon="inline-start" />
                  {titleForLocale(locale, "参数", "Parameter")}
                </Button>
              </div>
            </FieldGroup>
            <div className="min-h-0 flex-1 px-4 pb-4 sm:px-6">
              <PythonEditor
                value={draft.code}
                onChange={(code) =>
                  setDraft((current) => ({ ...current, code }))
                }
              />
            </div>
            <div className="grid shrink-0 gap-4 border-t px-4 py-4 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-end">
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
              <div className="flex items-center gap-3">
                {draft.parameters.some((item) => item.key.trim()) ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={titleForLocale(
                      locale,
                      "配置参数",
                      "Configure parameters",
                    )}
                    onClick={() => setTestParamsOpen(true)}
                  >
                    <Settings size={16} />
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="outline"
                  disabled={testing}
                  onClick={() => void testScript()}
                >
                  {titleForLocale(locale, "试跑", "Test")}
                </Button>
                {testResult ? (
                  <span className="font-mono text-xs text-muted-foreground">
                    {testResult.ok
                      ? `${testResult.balance} / ${testResult.total} ${testResult.currency}`
                      : testResult.error_message}
                  </span>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </section>
      <Dialog open={testParamsOpen} onOpenChange={setTestParamsOpen}>
        <AppDialogContent
          className="max-w-md"
          title={draft.name || titleForLocale(locale, "参数", "Parameters")}
          footer={
            <Button type="button" onClick={() => setTestParamsOpen(false)}>
              {titleForLocale(locale, "完成", "Done")}
            </Button>
          }
        >
          <FieldGroup>
            {draft.parameters
              .filter((item) => item.key.trim())
              .map((parameter) => (
                <Field key={parameter.key}>
                  <FieldLabel>{parameter.label || parameter.key}</FieldLabel>
                  <Input
                    value={testParams[parameter.key] ?? ""}
                    onChange={(event) =>
                      setTestParams((current) => ({
                        ...current,
                        [parameter.key]: event.target.value,
                      }))
                    }
                  />
                </Field>
              ))}
          </FieldGroup>
        </AppDialogContent>
      </Dialog>
    </>
  );
}
