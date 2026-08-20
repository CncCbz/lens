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
  relayLogBodyEnabled: boolean;
  relayLogDebugMode: boolean;
  modelListCompatModeEnabled: boolean;
  onProxyUrlChange: (value: string) => void;
  onCorsAllowOriginsChange: (value: string) => void;
  onRelayLogBodyEnabledChange: (checked: boolean) => void;
  onRelayLogDebugModeChange: (checked: boolean) => void;
  onModelListCompatModeEnabledChange: (checked: boolean) => void;
}

export function GatewaySettings({
  proxyUrl,
  corsAllowOrigins,
  relayLogBodyEnabled,
  relayLogDebugMode,
  modelListCompatModeEnabled,
  onProxyUrlChange,
  onCorsAllowOriginsChange,
  onRelayLogBodyEnabledChange,
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
      <Field
        orientation="horizontal"
        className="items-center justify-between gap-4"
      >
        <FieldContent>
          <FieldLabel className="w-auto">
            {titleForLocale(locale, "记录日志正文", "Record log body")}
          </FieldLabel>
        </FieldContent>
        <Switch
          checked={relayLogBodyEnabled}
          onCheckedChange={onRelayLogBodyEnabledChange}
        />
      </Field>
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
