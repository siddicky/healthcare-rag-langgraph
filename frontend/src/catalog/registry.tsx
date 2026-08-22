"use client";

import { defineRegistry } from "@json-render/react";
import { nymbleChatCatalog } from "./catalog";
import { useDispatchAction } from "./dispatch";
import { ActionCard } from "@/components/generative-ui/ActionCard";
import { InjectionTracker } from "@/components/generative-ui/InjectionTracker";
import { MiniCalendar } from "@/components/generative-ui/MiniCalendar";
import { TrendCard } from "@/components/generative-ui/TrendCard";
import { Button } from "@/components/core/Button";
import { Card } from "@/components/core/Card";
import { Label } from "@/components/core/Label";
import { Tag } from "@/components/core/Tag";
import { ScoreRing } from "@/components/data-display/ScoreRing";
import { StatRow } from "@/components/data-display/StatRow";
import { Timeline } from "@/components/data-display/Timeline";

/**
 * Registry adapter: the catalog's serializable `text`/`label`/`action` props
 * are remapped back to React children and handlers here (per
 * catalog.prompt.md). Handlers always resolve through the fixed dispatch map
 * (DispatchProvider context) — never a model-emitted function.
 */
export const { registry } = defineRegistry(nymbleChatCatalog, {
  components: {
    InjectionTracker: ({ props }) => <InjectionTracker {...props} />,
    MiniCalendar: ({ props }) => {
      const dispatch = useDispatchAction();
      return (
        <MiniCalendar
          monthLabel={props.monthLabel}
          firstWeekday={props.firstWeekday}
          daysInMonth={props.daysInMonth}
          highlights={props.highlights}
          onSelectDate={
            props.onDateSelectAction !== undefined
              ? (date: number) => {
                  void date;
                  dispatch(props.onDateSelectAction ?? "");
                }
              : undefined
          }
        />
      );
    },
    TrendCard: ({ props }) => <TrendCard {...props} />,
    ActionCard: ({ props }) => {
      const dispatch = useDispatchAction();
      return (
        <ActionCard
          title={props.title}
          body={props.body}
          primaryAction={
            props.primaryAction !== undefined
              ? {
                  label: props.primaryAction.label,
                  onClick: () => dispatch(props.primaryAction?.action ?? ""),
                }
              : undefined
          }
          secondaryAction={
            props.secondaryAction !== undefined
              ? {
                  label: props.secondaryAction.label,
                  onClick: () => dispatch(props.secondaryAction?.action ?? ""),
                }
              : undefined
          }
        />
      );
    },
    StatRow: ({ props }) => <StatRow {...props} />,
    ScoreRing: ({ props }) => <ScoreRing {...props} />,
    Timeline: ({ props }) => <Timeline {...props} />,
    Card: ({ props, children }) => (
      <Card variant={props.variant} bordered={props.bordered} large={props.large}>
        {props.text}
        {children}
      </Card>
    ),
    Tag: ({ props }) => <Tag>{props.text}</Tag>,
    Label: ({ props }) => <Label gold={props.gold}>{props.text}</Label>,
    Button: ({ props }) => {
      const dispatch = useDispatchAction();
      return (
        <Button
          variant={props.variant}
          size={props.size}
          full={props.full}
          disabled={props.disabled}
          onClick={() => dispatch(props.action)}
        >
          {props.label}
        </Button>
      );
    },
  },
});
