"use client";

import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { titleForLocale, useI18n } from "@/lib/i18n";

interface GatewaySettingsProps {
  proxyUrl: string;
  corsAllowOrigins: string;
  relayLogRequestHeadersEnabled: boolean;
  relayLogResponseHeadersEnabled: boolean;
  relayLogRequestBodyEnabled: boolean;
  relayLogResponseBodyEnabled: boolean;
  relayLogInputEnabled: boolean;
  relayLogOutputEnabled: boolean;
  relayLogDebugMode: boolean;
  modelListCompatModeEnabled: boolean;
  onProxyUrlChange: (value: string) => void;
  onCorsAllowOriginsChange: (value: string) => void;
  onRelayLogRequestHeadersEnabledChange: (checked: boolean) => void;
  onRelayLogResponseHeadersEnabledChange: (checked: boolean) => void;
  onRelayLogRequestBodyEnabledChange: (checked: boolean) => void;
  onRelayLogResponseBodyEnabledChange: (checked: boolean) => void;
  onRelayLogInputEnabledChange: (checked: boolean) => void;
  onRelayLogOutputEnabledChange: (checked: boolean) => void;
  onRelayLogDebugModeChange: (checked: boolean) => void;
  onModelListCompatModeEnabledChange: (checked: boolean) => void;
}

function LogSwitch({
  label,
  checked,
  onCheckedChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <Field
      orientation="horizontal"
      className="items-center justify-between gap-4"
    >
      <FieldContent>
        <FieldLabel className="w-auto">{label}</FieldLabel>
      </FieldContent>
      <Switch
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
      />
    </Field>
  );
}

export function GatewaySettings({
  proxyUrl,
  corsAllowOrigins,
  relayLogRequestHeadersEnabled,
  relayLogResponseHeadersEnabled,
  relayLogRequestBodyEnabled,
  relayLogResponseBodyEnabled,
  relayLogInputEnabled,
  relayLogOutputEnabled,
  relayLogDebugMode,
  modelListCompatModeEnabled,
  onProxyUrlChange,
  onCorsAllowOriginsChange,
  onRelayLogRequestHeadersEnabledChange,
  onRelayLogResponseHeadersEnabledChange,
  onRelayLogRequestBodyEnabledChange,
  onRelayLogResponseBodyEnabledChange,
  onRelayLogInputEnabledChange,
  onRelayLogOutputEnabledChange,
  onRelayLogDebugModeChange,
  onModelListCompatModeEnabledChange,
}: GatewaySettingsProps) {
  const { locale } = useI18n();

  return (
    <FieldGroup>
      <Field>
        <FieldLabel>
          {titleForLocale(locale, "全局代理地址", "Global proxy URL")}
        </FieldLabel>
        <Input
          value={proxyUrl}
          onChange={(event) => onProxyUrlChange(event.target.value)}
          placeholder="http://127.0.0.1:7890"
        />
      </Field>
      <Field>
        <FieldLabel>
          {titleForLocale(locale, "CORS 跨域名单", "CORS allow origins")}
        </FieldLabel>
        <Textarea
          className="min-h-[92px]"
          value={corsAllowOrigins}
          onChange={(event) => onCorsAllowOriginsChange(event.target.value)}
          placeholder="*\nhttp://localhost:3000"
        />
      </Field>
      <Field
        orientation="horizontal"
        className="items-center justify-between gap-4"
      >
        <FieldContent>
          <FieldLabel className="w-auto">
            {titleForLocale(
              locale,
              "模型列表兼容模式",
              "Model list compatibility mode",
            )}
          </FieldLabel>
          <FieldDescription>
            {titleForLocale(
              locale,
              "开启后 /v1/models 会以 OpenAI 格式列出全部协议模型；如果客户端不支持某协议,实际请求仍可能失败。",
              "When enabled, /v1/models lists all protocol models in OpenAI format; requests can still fail if the client cannot call a protocol.",
            )}
          </FieldDescription>
        </FieldContent>
        <Switch
          checked={modelListCompatModeEnabled}
          onCheckedChange={onModelListCompatModeEnabledChange}
        />
      </Field>
      <div>
        <div className="mb-2 text-sm font-medium">
          {titleForLocale(locale, "日志记录", "Log capture")}
        </div>
        <FieldGroup className="grid gap-4 sm:grid-cols-2">
          <LogSwitch
            label={titleForLocale(locale, "请求头", "Request headers")}
            checked={relayLogRequestHeadersEnabled}
            onCheckedChange={onRelayLogRequestHeadersEnabledChange}
          />
          <LogSwitch
            label={titleForLocale(locale, "响应头", "Response headers")}
            checked={relayLogResponseHeadersEnabled}
            onCheckedChange={onRelayLogResponseHeadersEnabledChange}
          />
          <LogSwitch
            label={titleForLocale(locale, "请求体", "Request body")}
            checked={relayLogRequestBodyEnabled}
            onCheckedChange={onRelayLogRequestBodyEnabledChange}
          />
          <LogSwitch
            label={titleForLocale(locale, "响应体", "Response body")}
            checked={relayLogResponseBodyEnabled}
            onCheckedChange={onRelayLogResponseBodyEnabledChange}
          />
          <LogSwitch
            label={titleForLocale(locale, "请求输入", "Request input")}
            checked={relayLogInputEnabled}
            onCheckedChange={onRelayLogInputEnabledChange}
            disabled={!relayLogRequestBodyEnabled}
          />
          <LogSwitch
            label={titleForLocale(locale, "响应输出", "Response output")}
            checked={relayLogOutputEnabled}
            onCheckedChange={onRelayLogOutputEnabledChange}
            disabled={!relayLogResponseBodyEnabled}
          />
        </FieldGroup>
      </div>
      <Field
        orientation="horizontal"
        className="items-center justify-between gap-4"
      >
        <FieldContent>
          <FieldLabel className="w-auto">
            {titleForLocale(locale, "日志调试模式", "Log debug mode")}
          </FieldLabel>
          <FieldDescription>
            {titleForLocale(
              locale,
              "开启后同时记录上游原始响应与客户端原始响应，日志体积会显著增大；默认只记录蒸馏后的响应内容。",
              "When enabled, stores raw upstream and client responses in addition to distilled content; log size grows significantly. By default only distilled response content is stored.",
            )}
          </FieldDescription>
        </FieldContent>
        <Switch
          checked={relayLogDebugMode}
          onCheckedChange={onRelayLogDebugModeChange}
        />
      </Field>
    </FieldGroup>
  );
}
