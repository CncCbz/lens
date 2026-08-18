"use client";

import { useEffect, useMemo, useState } from "react";
import {
	AlertCircle,
	ArrowDownToLine,
	ArrowUpFromLine,
	Clock3,
	Database,
	DollarSign,
	Fingerprint,
	Gauge,
	KeyRound,
	ServerCog,
	Upload,
	Waypoints,
	Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { RequestLogDetail, RequestLogItem } from "@/lib/api";
import { formatLogDateTime } from "@/lib/datetime";
import { ModelAvatar } from "@/lib/model-icons";
import { cn } from "@/lib/utils";
import {
	ProtocolBadge,
	RequestMeta,
	RequestMetric,
	RequestOutcomeBadge,
} from "./components";
import {
	formatChannelCredentialLabel,
	formatErrorDisplay,
	formatGatewayKeyLabel,
	formatMaybeCount,
	formatMaybeMoney,
	formatMs,
	formatTps,
	formatUserAgentDisplay,
	getModelChain,
	getResolvedGroupName,
	getSecondaryModelName,
	titleForLocale,
} from "./shared";

export function AttemptChain({
	detail,
	locale,
}: {
	detail: RequestLogDetail;
	locale: "zh-CN" | "en-US";
}) {
	const attempts = detail.attempts.length
		? detail.attempts
		: [
				{
					channel_id: detail.channel_id || "n/a",
					channel_name: detail.channel_name || detail.channel_id || "n/a",
					credential_id: null,
					credential_name: "",
					model_name:
						detail.upstream_model_name ||
						detail.resolved_group_name ||
						detail.requested_group_name ||
						null,
					status_code: detail.status_code,
					success: detail.success,
					duration_ms: detail.latency_ms,
					error_message: detail.error_message || null,
				},
			];

	return (
		<div className="overflow-hidden rounded-xl bg-muted/20">
			{attempts.map((attempt, index) => {
				const errorDisplay = formatErrorDisplay(attempt.error_message);
				return (
					<div
						key={`${attempt.channel_id}-${index}`}
						className={cn(
							"border-t px-4 first:border-t-0",
							errorDisplay ? "py-3" : "py-2.5",
						)}
					>
						<div className="flex flex-wrap items-center justify-between gap-3">
							<div className="flex min-w-0 flex-wrap items-center gap-2">
								<span className="inline-flex h-7 min-w-7 items-center justify-center rounded-full bg-background px-2 text-xs font-semibold text-muted-foreground">
									{index + 1}
								</span>
								{attempt.error_category ? (
									<Badge variant="outline" className="max-w-[140px] truncate">
										{attempt.error_category}
									</Badge>
								) : null}
								<span className="max-w-[220px] truncate text-sm font-medium text-foreground">
									{attempt.channel_name}
								</span>
								{attempt.credential_name || attempt.credential_id ? (
									<Badge variant="secondary" className="max-w-[160px] truncate">
										{attempt.credential_name || attempt.credential_id}
									</Badge>
								) : null}
								{attempt.model_name ? (
									<span className="max-w-[220px] truncate text-xs text-muted-foreground">
										{attempt.model_name}
									</span>
								) : null}
								<RequestOutcomeBadge
									status={attempt.success ? "succeeded" : "failed"}
									success={attempt.success}
									statusCode={attempt.status_code}
									locale={locale}
									errorMessage={errorDisplay}
								/>
								{attempt.retryable !== null &&
								attempt.retryable !== undefined ? (
									<Badge variant="secondary" className="max-w-[120px] truncate">
										{attempt.retryable
											? titleForLocale(locale, "可重试", "Retryable")
											: titleForLocale(locale, "不重试", "No retry")}
									</Badge>
								) : null}
								{attempt.provider_error_code ? (
									<Badge variant="outline" className="max-w-[160px] truncate">
										{attempt.provider_error_code}
									</Badge>
								) : null}
							</div>
							<div className="flex items-center gap-3 text-xs text-muted-foreground">
								{attempt.error_policy_key ? (
									<span>{`policy ${attempt.error_policy_key}`}</span>
								) : null}
								{attempt.retry_after_seconds ? (
									<span>{`Retry-After ${attempt.retry_after_seconds}s`}</span>
								) : null}
								<span>{formatMs(attempt.duration_ms)}</span>
							</div>
						</div>
						{errorDisplay ? (
							<div className="mt-2 rounded-md bg-destructive/10 px-3 py-2 text-xs whitespace-pre-wrap break-words text-destructive">
								{errorDisplay}
							</div>
						) : null}
					</div>
				);
			})}
		</div>
	);
}

function shortRequestId(value: string) {
	if (value.length <= 14) return value;
	return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

export function RequestCard({
	item,
	locale,
	timeZone,
	canOpenDetail,
	onOpenDetail,
	onOpenAttempts,
}: {
	item: RequestLogItem;
	locale: "zh-CN" | "en-US";
	timeZone?: string;
	canOpenDetail: boolean;
	onOpenDetail: () => void;
	onOpenAttempts: () => void;
}) {
	const primaryModelName = getResolvedGroupName(item);
	const modelChain = getModelChain(item);
	const modelDisplayName = item.reasoning_effort
		? `${modelChain} ${item.reasoning_effort}`
		: modelChain;
	const secondaryModelName = getSecondaryModelName(item);
	const attemptCount = Number.isFinite(item.attempt_count)
		? item.attempt_count
		: 0;
	const errorDisplay = formatErrorDisplay(item.error_message);
	const showAttemptsButton = attemptCount > 1 || Boolean(errorDisplay);
	const running =
		item.lifecycle_status === "connecting" ||
		item.lifecycle_status === "streaming";
	const [now, setNow] = useState(() => Date.now());
	const createdAtMs = useMemo(
		() => new Date(item.created_at).getTime(),
		[item.created_at],
	);

	useEffect(() => {
		if (!running) return;
		const timer = window.setInterval(() => setNow(Date.now()), 1000);
		return () => window.clearInterval(timer);
	}, [running]);

	const elapsedMs = running
		? Math.max(now - createdAtMs, item.latency_ms || 0, 0)
		: item.latency_ms;

	return (
		<Card
			className={cn(
				"rounded-2xl py-0 transition-colors",
				canOpenDetail ? "hover:bg-muted/20" : "",
				item.lifecycle_status === "failed"
					? "border-destructive/25 bg-destructive/[0.015]"
					: "",
			)}
		>
			<div
				role={canOpenDetail ? "button" : undefined}
				tabIndex={canOpenDetail ? 0 : undefined}
				onClick={canOpenDetail ? onOpenDetail : undefined}
				onKeyDown={(event) => {
					if (!canOpenDetail) return;
					if (event.key === "Enter" || event.key === " ") {
						event.preventDefault();
						onOpenDetail();
					}
				}}
				className={cn(
					"grid w-full min-w-0 grid-cols-[minmax(0,1fr)] items-start gap-x-3 gap-y-2.5 px-3 py-3 sm:grid-cols-[56px_minmax(0,1fr)]",
					canOpenDetail
						? "cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
						: "cursor-default",
				)}
			>
				<div className="hidden size-12 items-center justify-center self-start rounded-2xl border bg-muted/40 sm:flex">
					<ModelAvatar name={primaryModelName} size={28} />
				</div>

				<div className="grid min-w-0 gap-2.5">
					<div className="grid gap-2">
						<div className="flex min-w-0 flex-wrap items-center gap-2">
							<div className="min-w-0 max-w-full truncate text-[15px] font-semibold leading-6 text-foreground">
								{modelDisplayName}
							</div>
							<ProtocolBadge protocol={item.protocol} />
							<RequestOutcomeBadge
								status={item.lifecycle_status}
								success={item.success}
								statusCode={item.status_code}
								locale={locale}
								errorMessage={errorDisplay}
							/>
							{item.request_id ? (
								<Badge
									variant="secondary"
									className="max-w-[150px] truncate font-mono text-[11px]"
									title={item.request_id}
								>
									{shortRequestId(item.request_id)}
								</Badge>
							) : null}
							{showAttemptsButton ? (
								<Button
									type="button"
									variant="outline"
									size="sm"
									className="h-7 rounded-full px-2.5 text-xs"
									onClick={(event) => {
										event.stopPropagation();
										onOpenAttempts();
									}}
								>
									<Waypoints data-icon="inline-start" />
									{attemptCount > 1
										? titleForLocale(
												locale,
												`链路 ${attemptCount}`,
												`Attempts ${attemptCount}`,
											)
										: titleForLocale(locale, "错误详情", "Error detail")}
								</Button>
							) : null}
						</div>

						{errorDisplay ? (
							<button
								type="button"
								className="flex max-w-full items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-left text-xs text-destructive hover:bg-destructive/15"
								onClick={(event) => {
									event.stopPropagation();
									onOpenAttempts();
								}}
							>
								<AlertCircle size={14} className="mt-0.5 shrink-0" />
								<span className="line-clamp-3 whitespace-pre-wrap break-words">
									{errorDisplay}
								</span>
							</button>
						) : null}

						<div className="flex flex-wrap items-center gap-2">
							<RequestMeta
								icon={<Clock3 size={13} />}
								value={formatLogDateTime(item.created_at, locale, timeZone)}
								className="pl-0"
							/>
							<RequestMeta
								icon={<Waypoints size={13} />}
								value={formatChannelCredentialLabel(item)}
							/>
							{item.gateway_key_id && item.gateway_has_multiple_keys ? (
								<RequestMeta
									icon={<KeyRound size={13} />}
									value={formatGatewayKeyLabel(item, locale)}
								/>
							) : null}
							{item.user_agent ? (
								<RequestMeta
									icon={<Fingerprint size={13} />}
									value={formatUserAgentDisplay(item.user_agent, locale)}
									tooltip={item.user_agent}
									className="sm:max-w-[360px]"
								/>
							) : null}
							{secondaryModelName ? (
								<RequestMeta
									icon={<ServerCog size={13} />}
									value={secondaryModelName}
								/>
							) : null}
						</div>
					</div>

					<div className="grid w-full grid-cols-[repeat(auto-fit,minmax(110px,1fr))] gap-1.5">
						<RequestMetric
							icon={<Zap size={14} />}
							label={titleForLocale(locale, "首字延迟", "First token")}
							value={formatMs(item.first_token_latency_ms)}
						/>
						<RequestMetric
							icon={<ServerCog size={14} />}
							label={titleForLocale(locale, "总耗时", "Total")}
							value={formatMs(elapsedMs)}
						/>
						<RequestMetric
							icon={<Gauge size={14} />}
							label={titleForLocale(locale, "TPS", "TPS")}
							value={formatTps(item.tokens_per_second, running)}
						/>
						<RequestMetric
							icon={<ArrowDownToLine size={14} />}
							label={titleForLocale(locale, "输入", "Input")}
							value={formatMaybeCount(item.input_tokens, running)}
						/>
						<RequestMetric
							icon={<ArrowUpFromLine size={14} />}
							label={titleForLocale(locale, "输出", "Output")}
							value={formatMaybeCount(item.output_tokens, running)}
						/>
						<RequestMetric
							icon={<Database size={14} />}
							label={titleForLocale(locale, "缓存读取", "Cache Read")}
							value={formatMaybeCount(item.cache_read_input_tokens, running)}
						/>
						<RequestMetric
							icon={<Upload size={14} />}
							label={titleForLocale(locale, "缓存写入", "Cache Write")}
							value={formatMaybeCount(item.cache_write_input_tokens, running)}
						/>
						<RequestMetric
							icon={<DollarSign size={14} />}
							label={titleForLocale(locale, "费用", "Cost")}
							value={formatMaybeMoney(item.total_cost_usd, running)}
							valueClassName="whitespace-nowrap break-normal text-[12px]"
						/>
					</div>
				</div>
			</div>
		</Card>
	);
}
