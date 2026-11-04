# Segment canvas blocks: the toolbox of the Select Members screen

Connections: a block's `previous` check must share a name with the check of the statement input (or `next`) it
plugs into; `output` blocks plug into value inputs. `any` = no check. You set fields (and `picked` codes for a
lookup popup); the tools fill the rest as the screen does.

## targeting_holder  (Select)
- statement inputs: CONDITIONS [any]

## or  (Select)
- previous: OR|If
- next: OR|If
- statement inputs: OPERAND1 [If|Filter|OR], OPERAND2 [If|Filter|OR]
- seen inside: targeting_holder.CONDITIONS (2)

## targeting_condition  (Select)
- previous: any
- next: any
- value inputs: vector_counter_namespace [any], RHS_INPUT [any], RHS_VALUE [any]
- fields: attr_level, parent_operator, mto_attribute_visible_value, mto_attriubte_value, mto_attirbute, date_operator, operators, attribute, selected_subscription_plan, job_id, card_annual_spend, selected_subscription_attribute, datetime_operator, RhsSelectedOption, attribute2, segment_rhs_attribute, segment_visible_value, mto_rhs_offer_attirbute, rhsdateoptionselected, value, customBlockOption, customdateopt1, datevalue, customdatedropdownopts2, tiervalidmnths, tieronlydateoptions, visible_value, tierslevel, tiermainoption, tierlevelnamevalue, parent_value, child_operator, card_setup_id, selected_bank_attribute, selected_card_attribute, COUNTER, COUNTER_TYPE_INPUT, selected_partner_program, selected_partner_program_attribute
- operator between: its first value in RHS_INPUT, its second in RHS_VALUE (shown after "and")
- a LOOKUP / LOV attribute's codes: operators in, not_in take them in `picked` (the value popup); any other operator (==, !=) takes one code in the `value` field (a dropdown)
- seen inside: targeting_holder.CONDITIONS (74), or.OPERAND1 (2), or.OPERAND2 (1)
- saved picked codes: 1,9 / 11 / 256
- saved fields: attr_level=MA_CC/MA_P/MA_SP/MA_T; attribute=3_balance/6_balance/age/date_of_joining/gender/membership_stage; operators=</==/>/>=/not_null/null; date_operator=CALENDAR_DATE/DATE/YEAR; tierslevel=Elite_Levels; tiermainoption=name/tier_start_date/tier_validity; tierlevelnamevalue=Bronze/Stone; datetime_operator=CALENDAR_DATE/DATE/DAY_OF_MONTH/MONTH/YEAR; card_setup_id=1/ACTIVE_CARDS; selected_card_attribute=closing_date; selected_subscription_plan=1; selected_subscription_attribute=plan_start_date/plan_status

## targeting_member_attribute  (Select)
- output: Number
- value inputs: vector_counter_namespace [any], RHS_INPUT [any], RHS_VALUE [any]
- fields: ATTRIBUTE_TYPE, ATTRIBUTE, selected_subscription_plan, segment_id, date_operator, datetime_operator, selected_subscription_attribute, selected_partner_program_attribute, selected_card_attribute_targeted, tieronlydateoptions, job_id, mto_attribute_visible_value, mto_attriubte_value, mto_attribute, visible_value, value
- operator between: its first value in RHS_INPUT, its second in RHS_VALUE (shown after "and")
- a LOOKUP / LOV attribute's codes: operators in, not_in take them in `picked` (the value popup); any other operator (==, !=) takes one code in the `value` field (a dropdown)
- seen inside: targeting_condition.RHS_INPUT (2)
- saved fields: ATTRIBUTE_TYPE=MA_CC/MA_SP; ATTRIBUTE=9; selected_card_attribute_targeted=closing_date; date_operator=CALENDAR_DATE; selected_subscription_plan=1; selected_subscription_attribute=plan_status

## static_value  (Select)
- output: Static Value
- fields: value
- value: the screen accepts only a value matching `^[0-9]*\.?[0-9]+$`; any other value is a screen error ("<value> is not valid number")
- seen inside: targeting_condition.RHS_INPUT (22)
- saved fields: value=1/18/1950/1980/1999/20

## static_string_value  (Select)
- output: Static String Value
- fields: value

## static_date  (Select)
- output: Static date
- fields: value
- value: written in the format `YYYY-MM-DD` (dayjs tokens), as the screen's picker writes it
- seen inside: targeting_condition.RHS_INPUT (9), targeting_condition.RHS_VALUE (1)
- saved fields: value=2024-06-01/2025-11-04/2026-02-01/2026-02-04/2026-06-01/2026-06-09

## current_date_block  (Select)
- output: Current Date
- fields: current_date_type

## currency_converter  (Select)
- output: Currency Converter
- fields: conversion_type, LOYALTY_ACCOUNT

## arithmetic_generic  (Select)
- output: Arithmetic
- value inputs: LHS [Static Value|Activity Attribute|Product Attribute|Payment Attribute|Number|Currency Converter|Arithmetic|Blueprint Variable|Current Date], RHS [Static Value|Activity Attribute|Product Attribute|Payment Attribute|Number|Currency Converter|Arithmetic|Blueprint Variable|Current Date]
- fields: operator

## arithmetic_date  (Select)
- output: Arithmetic date
- value inputs: LHS [Activity Attribute|Current Date|Product Attribute|Payment Attribute|Number|Static date|Static date time|Static String Value|Arithmetic date|Blueprint Variable]
- fields: operator, number, date_quantity, snap_to_quntity

