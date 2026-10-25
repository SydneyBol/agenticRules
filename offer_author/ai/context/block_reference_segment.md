# Segment canvas block reference (generated from gravty-ui source by tools/build/block_reference.py; do not edit)
Toolbox: src/app/workspaces/program/screens/segment-setup/config/segmentation-blockly.config.ts

Connections: a block's `previous` check must share a name with the check of the statement input (or `next`) it
plugs into; `output` blocks plug into value inputs. `any` = no check. You set fields (and `picked` codes for a
lookup popup); the tools write every mutation attribute from them as the UI does.

## targeting_holder  (Select; TargetingHolderSegmentBlock, blocks/segment-level/targeting-holder-segment.block.ts)
- statement inputs: CONDITIONS [any]

## or  (Select; OrBlock, blocks/offer-level/or.block.ts)
- previous: OR|If
- next: OR|If
- statement inputs: OPERAND1 [If|Filter|OR], OPERAND2 [If|Filter|OR]
- seen inside: targeting_holder.CONDITIONS (2)

## targeting_condition  (Select; TargetingConditionSegmentBlock, blocks/segment-level/targeting-condition-segment.block.ts)
- previous: any
- next: any
- value inputs: vector_counter_namespace [any], RHS_INPUT [any], RHS_VALUE [any]
- fields: attr_level, parent_operator, mto_attribute_visible_value, mto_attriubte_value, mto_attirbute, date_operator, operators, attribute, selected_subscription_plan, job_id, card_annual_spend, selected_subscription_attribute, datetime_operator, RhsSelectedOption, attribute2, segment_rhs_attribute, segment_visible_value, mto_rhs_offer_attirbute, rhsdateoptionselected, value, customBlockOption, customdateopt1, datevalue, customdatedropdownopts2, tiervalidmnths, tieronlydateoptions, visible_value, tierslevel, tiermainoption, tierlevelnamevalue, parent_value, child_operator, card_setup_id, selected_bank_attribute, selected_card_attribute, COUNTER, COUNTER_TYPE_INPUT, selected_partner_program, selected_partner_program_attribute
- seen inside: targeting_holder.CONDITIONS (74), or.OPERAND1 (2), or.OPERAND2 (1)
- saved picked codes: 1,9 / 11 / 256
- saved fields: attr_level=MA_CC/MA_P/MA_SP/MA_T; attribute=3_balance/6_balance/age/date_of_joining/gender/membership_stage; operators=</==/>/>=/not_null/null; date_operator=CALENDAR_DATE/DATE/YEAR; value=1/239/256/268/female/male; tierslevel=Elite_Levels; tiermainoption=name/tier_start_date/tier_validity; tierlevelnamevalue=Bronze/Stone; datetime_operator=CALENDAR_DATE/DATE/DAY_OF_MONTH/MONTH/YEAR; card_setup_id=1/ACTIVE_CARDS; selected_card_attribute=closing_date; selected_subscription_plan=1; selected_subscription_attribute=plan_start_date/plan_status

## targeting_member_attribute  (Select; TargetingMemberAttributeSegmentBlock, blocks/segment-level/member-attribute-segment.block.ts)
- output: Number
- value inputs: vector_counter_namespace [any], RHS_INPUT [any], RHS_VALUE [any]
- fields: ATTRIBUTE_TYPE, ATTRIBUTE, selected_subscription_plan, segment_id, date_operator, datetime_operator, selected_subscription_attribute, selected_partner_program_attribute, selected_card_attribute_targeted, tieronlydateoptions, job_id, mto_attribute_visible_value, mto_attriubte_value, mto_attribute, visible_value, value
- seen inside: targeting_condition.RHS_INPUT (2)
- saved fields: ATTRIBUTE_TYPE=MA_CC/MA_SP; ATTRIBUTE=9; selected_card_attribute_targeted=closing_date; date_operator=CALENDAR_DATE; selected_subscription_plan=1; selected_subscription_attribute=plan_status

## static_value  (Select; StaticValueBlock, blocks/attributes-variables/static-value.block.ts)
- output: Static Value
- fields: value
- seen inside: targeting_condition.RHS_INPUT (22)
- saved fields: value=1/18/1950/1980/1999/20

## static_string_value  (Select; StaticStringValueBlock, blocks/attributes-variables/static-string-value.block.ts)
- output: Static String Value
- fields: value

## static_date  (Select; StaticDateBlock, blocks/attributes-variables/static-date.block.ts)
- output: Static date
- fields: value
- seen inside: targeting_condition.RHS_INPUT (9), targeting_condition.RHS_VALUE (1)
- saved fields: value=2024-06-01/2025-11-04/2026-02-01/2026-02-04/2026-06-01/2026-06-09

## current_date_block  (Select; CurrentDateBlock, blocks/attributes-variables/current-date.block.ts)
- output: Current Date
- fields: current_date_type

## currency_converter  (Select; CurrencyConvertorRateBlock, blocks/attributes-variables/currency-convertor-rate.block.ts)
- output: Currency Converter
- fields: conversion_type, LOYALTY_ACCOUNT

## arithmetic_generic  (Select; ArithmeticGenericBlock, blocks/attributes-variables/arithmetic-generic.block.ts)
- output: Arithmetic
- value inputs: LHS [Static Value|Activity Attribute|Product Attribute|Payment Attribute|Number|Currency Converter|Arithmetic|Blueprint Variable|Current Date], RHS [Static Value|Activity Attribute|Product Attribute|Payment Attribute|Number|Currency Converter|Arithmetic|Blueprint Variable|Current Date]
- fields: operator

## arithmetic_date  (Select; ArithmeticDateBlock, blocks/attributes-variables/arithmetic-date.block.ts)
- output: Arithmetic date
- value inputs: LHS [Activity Attribute|Current Date|Product Attribute|Payment Attribute|Number|Static date|Static date time|Static String Value|Arithmetic date|Blueprint Variable]
- fields: operator, number, date_quantity, snap_to_quntity

