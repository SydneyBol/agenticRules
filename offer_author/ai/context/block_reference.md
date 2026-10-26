# Rules canvas block reference (generated from gravty-ui source by tools/build/block_reference.py; do not edit)
Toolbox: src/app/workspaces/offer/modules/offer-setup/screens/offer-rules/config/offer-rules.config.ts

Connections: a block's `previous` check must share a name with the check of the statement input (or `next`) it
plugs into; `output` blocks plug into value inputs. `any` = no check. You set fields (and `picked` codes for a
lookup popup); the tools write every mutation attribute from them as the UI does.

## offer_generic  (Offer Level Block; OfferGenericBlock, blocks/offer-level/offer-generic.block.ts)
- statement inputs: activities [Activity], events [Event]

## activity  (Offer Level Block; OfferActivityBlock, blocks/offer-level/offer-activity.block.ts)
- previous: Activity
- next: Activity
- statement inputs: ACTIVITY_CRITERIA [Filter|OR], RULES [Rule]
- seen inside: offer_generic.activities (27)

## activity_criteria  (Offer Level Block; FilterBlock, blocks/offer-level/filter.block.ts)
- previous: Filter|OR
- next: Filter|OR
- value inputs: RHS_INPUT [any], RHS_VALUE [any], vector_counter_namespace [any]
- fields: OBJECT, attribute, attribute_level, airporttypes, selected_partner_program, fb_pages, operators, value, target_type_value, mto_ladder_value, vector_counter_name, vector_counter_namespace, attribute_entity_map_level, index_datatype, dummy_date_value, date_operator, datetime_operator, tiers, selected_partner_program_attribute, payment_operators, visible_value, blueprinttypes, selected_subscription_plan, selected_subscription_attribute, card_main_option, activecard_mainoperators, onlycard_second_option, cardvalue, card_annual_option, cardannualr_opperater, tierdefault, loyaltyaccount, opperater, opperatervalue, PAYMENT_MODE, payment_visible_value, payment_value
- live dropdowns: OBJECT [airport, airport_pair, activity, location, member, sponsor]
  - OBJECT=airport adds: airporttypes [destination, origin]
  - OBJECT=airport_pair adds: attribute (tenant catalog)
  - OBJECT=activity adds: attribute (tenant catalog)
  - OBJECT=location adds: attribute (tenant catalog)
  - OBJECT=member adds: attribute_level [MA_A, MA_CA, MA_CC, MP_S, MA_O, MA_P, MA_SP]
  - OBJECT=sponsor adds: attribute (tenant catalog)
- seen inside: activity.ACTIVITY_CRITERIA (29)
- saved picked codes: activity / activity_type / bit_type
- saved fields: OBJECT=activity/airport; operators===/between/in; value=BOOKING/activity/activity_/activity_1/activity_type/airport_name; airporttypes=origin; date_operator=DATE

## event  (Offer Level Block; EventBlock, blocks/offer-level/event.block.ts)
- previous: Event
- next: Event
- statement inputs: RULES [Rule]
- seen inside: offer_generic.events (3)

## or  (Offer Level Block; OrBlock, blocks/offer-level/or.block.ts)
- previous: OR|If
- next: OR|If
- statement inputs: OPERAND1 [If|Filter|OR], OPERAND2 [If|Filter|OR]

## rule  (Rule Level Block; RuleBlock, blocks/rule-level/rule.block.ts)
- previous: Billing Rule|Billing Action|Billing Rate / Rule|Action
- next: Billing Rule|Billing Action|Billing Rate / Rule|Action
- statement inputs: CONDITION [If|OR], ACTION [Billing Action|Billing Rate|Action|Rule]
- fields: IDENTIFIER
- seen inside: activity.RULES (63), event.RULES (9), rule.ACTION (7)
- saved fields: IDENTIFIER=Calculate Possible Points that can be ea/For Points Earned > Points Cap/Reward for Transacting in Birthday Month/Reward for Transacting in Wedding Annive/Reward for Transacting on Birthday/Reward for Transacting on Wedding Annive

## rule_line  (Rule Level Block; LineRuleBlock, blocks/rule-level/line-rule.block.ts)
- previous: Rule|Action
- next: Rule|Action
- statement inputs: CONDITION [If|OR], ACTION [Action]
- fields: IDENTIFIER
- seen inside: activity.RULES (22)
- saved fields: IDENTIFIER=Track Menu 1 Purchase/Track Purchase in Product Category 1/Track Purchase in Product Category 2/Track Purchase on 1st Eligible Product/Track and Reward on 2nd Eligible Categor/Track and Reward on 3rd Eligible Categor

## payment_rule  (Rule Level Block; PaymentRuleBlock, blocks/rule-level/payment-rule.block.ts)
- previous: Rule
- next: Rule
- statement inputs: CONDITION [If Payment], ACTION [Action]
- fields: IDENTIFIER

## if_generic_adaptive  (Rule Level Block; IfGenericBlock, blocks/rule-level/if-generic.block.ts)
- previous: If
- next: If
- value inputs: RHS_INPUT [any], RHS_VALUE [any], vector_counter_namespace [any]
- fields: OBJECT, value, attribute, attribute_level, third_parties_dropdown, ProductOrOfferContext, airporttypes, name, visible_value, blueprinttypes, selected_subscription_plan, selected_partner_program, selected_subscription_attribute, fb_pages, operators, target_type_value, mto_ladder_value, vector_counter_name, vector_counter_namespace, attribute_entity_map_level, index_datatype, dummy_date_value, date_operator, datetime_operator, tiers, selected_partner_program_attribute, payment_operators, card_main_option, activecard_mainoperators, onlycard_second_option, cardvalue, card_annual_option, cardannualr_opperater, PAYMENT_MODE, payment_visible_value, payment_value, tierdefault, loyaltyaccount, opperater, opperatervalue
- live dropdowns: OBJECT [third_party, airport, airport_pair, activity, location, member, products_attrs, sponsor]
  - OBJECT=third_party adds: third_parties_dropdown [ACTIVITY_REFERRER, COMPANION, CORPORATE, EMPLOYEE, ENROLLMENT_REFERRER, FAMILY_HEAD, INSTITUTION, TRANSFEREE]; attribute_level [MA_A, MA_S, MA_CA, MA_CC, MA_O, MA_P, MA_SP]
  - OBJECT=airport adds: airporttypes [destination, origin]
  - OBJECT=airport_pair adds: attribute (tenant catalog)
  - OBJECT=activity adds: attribute (tenant catalog)
  - OBJECT=location adds: attribute (tenant catalog)
  - OBJECT=member adds: attribute_level [MA_A, MA_S, MA_CA, MA_CC, MP_S, MA_O, MA_P, MA_SP]
  - OBJECT=products_attrs adds: ProductOrOfferContext [products_attrs, product_cluster, product_offer_attributes]; attribute (tenant catalog)
  - OBJECT=sponsor adds: attribute (tenant catalog)
- seen inside: rule.CONDITION (105), rule_line.CONDITION (29)
- saved picked codes: eligible_product_category_ / eligible_product_category_2 / eligible_product_category_3 / menu_1 / menu_2 / menu_3
- saved fields: OBJECT=activity/member/products_attrs/sponsor; datetime_operator=DAY_OF_WEEK/DAY_OF_YEAR/HOUR_OF_DAY/MONTH; operators=!=/</<=/==/>/in; attribute_level=MA_A/MA_O; ProductOrOfferContext=products_attrs; value=PAYMENT_WITH_POINTS/Yes/activity/target_product_1/target_product_2/ 1 ; date_operator=MONTH; vector_counter_name=unique/ Activity Type / Destination Airport / H_Sponsor ID / Line Product / Sponsor ; vector_counter_namespace=h_bit_type/h_destination_airport/h_location/h_sponsor_id/l_product_external_id

## if_payment_attribute  (Rule Level Block; IfPaymentBlock, blocks/rule-level/if-payment.block.ts)
- previous: If Payment
- next: If Payment
- value inputs: RHS_INPUT [any], RHS_VALUE [any]
- fields: payment_attribute, date_operator, operators, datetime_operator, value, visible_value, PAYMENT_MODE
- live dropdowns: payment_attribute (tenant catalog)

## action  (Rule Level Block; ActionBlock, blocks/rule-level/actionblock.block.ts)
- previous: Action
- next: Action
- value inputs: FORMULA [any], LOYALTY_ACCOUNT [any], PRIVILEGE_types [any], EXTEND_PRIVILEGE [any], TransferPoints [any], SWEEPSTAKES_LIST [any], PLAY_WIN_TYPE [any], RHS_INPUT [any], REWARD_TAGS [any], RHS_INPUT_CUSTOMTIEREND [Number|Arithmetic date|Static date|Static date time|Activity Attribute|Tiers|Member|current_date_block|Current Date|Blueprint Variable], vector_counter_namespace [any], RHS_INPUT_VALUE [any], DYNAMIC_RHS_INPUT [Number], PRIVILEGE [any], PRIVILEGE_USAGEPOLICY [any], PRIVILEGE_QUANTITY [any], VALIDITY_START_DATE [any], VALIDITY_END_DATE [any]
- fields: action_type, LOYALTY_ACCOUNT, SELECTED_PRIVILEGE, mta_type_value, WHAT, SELECTED_EXPIRE_LOYALTY_ACC, sweepstakeid, playwintype, playwinsetup, playwinusagepolicy, selected_card_id, SELECTED_EXP_OPERATOR, tiername, tierenddateopt, REWARD_TAGS, PENDING_DAYS_VALUE, mta_attribute, value, action_operator, vector_counter_name, vector_counter_namespace, attribute_entity_map_level, index_datatype, target_type_value, mto_ladder_value, PRIVILEGE_USAGEPOLICY, Privilege_Quantity, previlege_Validity, dynamic_privilege_config, visible_value, blueprinttypes
- live dropdowns: action_type [assign_play_win_reward, give_privilege, assign_sweepstakes, expire_points, take_points, reset_points, return_points, give_points, update_attribute]
  - action_type=assign_play_win_reward adds: playwintype [SCRATCH_CARD, SPIN_THE_WHEEL]
  - action_type=give_privilege adds: SELECTED_PRIVILEGE (tenant catalog); PRIVILEGE_USAGEPOLICY (tenant catalog); <value:PRIVILEGE_QUANTITY> [Number, Static Value, Activity Attribute, Product Attribute, Payment Attribute, Arithmetic, Blueprint Variable]; previlege_Validity [product_validity, custom_validity]
  - action_type=assign_sweepstakes adds: sweepstakeid []
  - action_type=expire_points adds: SELECTED_EXPIRE_LOYALTY_ACC (tenant catalog)
  - action_type=take_points adds: LOYALTY_ACCOUNT (tenant catalog); <value:FORMULA> [Number, Static Value, Activity Attribute, Product Attribute, Currency Converter, Arithmetic, Payment Attribute, Blueprint Variable]
  - action_type=reset_points adds: LOYALTY_ACCOUNT (tenant catalog)
  - action_type=return_points adds: LOYALTY_ACCOUNT (tenant catalog); <value:FORMULA> [Number, Static Value, Activity Attribute, Product Attribute, Currency Converter, Arithmetic, Payment Attribute, Blueprint Variable]
  - action_type=give_points adds: LOYALTY_ACCOUNT (tenant catalog); REWARD_TAGS (tenant catalog); <value:FORMULA> [Number, Static Value, Activity Attribute, Product Attribute, Currency Converter, Arithmetic, Payment Attribute, Blueprint Variable]
  - action_type=update_attribute adds: mta_type_value (tenant catalog)
- seen inside: rule.ACTION (104), rule_line.ACTION (31)
- saved fields: action_type=give_points/give_privilege/update_attribute; action_operator=INCREMENT_BY/RESET/SET; previlege_Validity=product_validity; value=; vector_counter_name=Sponsor/ Activity Type / Destination Airport / H_Sponsor ID / Line Product / Location ; vector_counter_namespace=h_bit_type/h_destination_airport/h_location/h_sponsor_id/l_product_external_id

## action_third_party  (Rule Level Block; ThirdPartyActionBlock, blocks/rule-level/thirdparty-action.block.ts)
- previous: Action
- next: Action
- value inputs: LOYALTY_ACCOUNT [any], PRIVILEGE_types [any], FORMULA [any], TransferPoints [any], PLAY_WIN_TYPE [any], SWEEPSTAKES_LIST [any], RHS_INPUT [Number|Arithmetic date|Static date|Activity Attribute|Tiers|Member|Current Date|Blueprint Variable], RHS_INPUT_CUSTOMTIEREND [Number|Arithmetic date|Static date|Activity Attribute|Tiers|Member|Current Date|Blueprint Variable], REWARD_TAGS [any], vector_counter_namespace [any], RHS_INPUT_VALUE [any], PRIVILEGE_USAGEPOLICY [any], PRIVILEGE [any], PRIVILEGE_QUANTITY [any], VALIDITY_START_DATE [any], VALIDITY_END_DATE [any]
- fields: third_parties_dropdown, action_type, LOYALTY_ACCOUNT, tierclassid, SELECTED_PRIVILEGE, mta_type_value, WHAT, Transfer_P_L, playwintype, sweepstakesetup, playwinsetup, playwinusagepolicy, selected_card_id, tierlevelid, tierenddateopt, REWARD_TAGS, PENDING_DAYS_VALUE, mta_attribute, value, action_operator, vector_counter_name, vector_counter_namespace, attribute_entity_map_level, index_datatype, target_type_value, mto_ladder_value, PRIVILEGE_USAGEPOLICY, Privilege_Quantity, previlege_Validity
- live dropdowns: third_parties_dropdown [ACTIVITY_REFERRER, COMPANION, CORPORATE, EMPLOYEE, ENROLLMENT_REFERRER, FAMILY_HEAD, INSTITUTION, TRANSFEREE]
  - third_parties_dropdown=ACTIVITY_REFERRER adds: action_type [assign_play_win_reward, give_privilege, assign_sweepstake, give_points, transfer_points, update_attribute]
  - third_parties_dropdown=COMPANION adds: action_type [assign_play_win_reward, give_privilege, assign_sweepstake, give_points, transfer_points, update_attribute]
  - third_parties_dropdown=CORPORATE adds: action_type [assign_play_win_reward, give_privilege, assign_sweepstake, give_points, transfer_points, update_attribute]
  - third_parties_dropdown=EMPLOYEE adds: action_type [assign_play_win_reward, give_privilege, assign_sweepstake, give_points, transfer_points, update_attribute]
  - third_parties_dropdown=ENROLLMENT_REFERRER adds: action_type [assign_play_win_reward, give_privilege, assign_sweepstake, give_points, transfer_points, update_attribute]
  - third_parties_dropdown=FAMILY_HEAD adds: action_type [assign_play_win_reward, give_privilege, assign_sweepstake, give_points, transfer_points, update_attribute]
  - third_parties_dropdown=INSTITUTION adds: action_type [assign_play_win_reward, give_privilege, assign_sweepstake, give_points, transfer_points, update_attribute]
  - third_parties_dropdown=TRANSFEREE adds: action_type [transfer_points, transfer_privilege]

## static_value  (Attributes & Variables; StaticValueBlock, blocks/attributes-variables/static-value.block.ts)
- output: Static Value
- fields: value
- seen inside: action.RHS_INPUT_VALUE (38), if_generic_adaptive.RHS_INPUT (15), arithmetic_generic.RHS (3)
- saved fields: value=0/1/100/2/3/4

## static_string_value  (Attributes & Variables; StaticStringValueBlock, blocks/attributes-variables/static-string-value.block.ts)
- output: Static String Value
- fields: value
- seen inside: action.RHS_INPUT (27)
- saved fields: value=Yes

## arithmetic_generic  (Attributes & Variables; ArithmeticGenericBlock, blocks/attributes-variables/arithmetic-generic.block.ts)
- output: Arithmetic
- value inputs: LHS [Static Value|Activity Attribute|Product Attribute|Payment Attribute|Number|Currency Converter|Arithmetic|Blueprint Variable|Current Date], RHS [Static Value|Activity Attribute|Product Attribute|Payment Attribute|Number|Currency Converter|Arithmetic|Blueprint Variable|Current Date]
- fields: operator
- live dropdowns: operator [/, *, +, -, %]
- seen inside: action.FORMULA (14), action.RHS_INPUT_VALUE (5), if_generic_adaptive.RHS_INPUT (3), arithmetic_generic.LHS (2)
- saved fields: operator=*/+/-//

## arithmetic_date  (Attributes & Variables; ArithmeticDateBlock, blocks/attributes-variables/arithmetic-date.block.ts)
- output: Arithmetic date
- value inputs: LHS [Activity Attribute|Current Date|Product Attribute|Payment Attribute|Number|Static date|Static date time|Static String Value|Arithmetic date|Blueprint Variable]
- fields: operator, number, date_quantity, snap_to_quntity
- live dropdowns: operator [+, -, snapTO]; date_quantity [DAYS, MONTHS, YEARS]
  - operator=snapTO adds: snap_to_quntity [MONTH, YEAR]

## current_date_block  (Attributes & Variables; CurrentDateBlock, blocks/attributes-variables/current-date.block.ts)
- output: Current Date
- fields: current_date_type
- live dropdowns: current_date_type [DATE, DATETIME, DAY_OF_MONTH, DAY_OF_WEEK, DAY_OF_YEAR, HOUR_OF_DAY, MINUTE_OF_HOUR, MONTH, WEEK_OF_YEAR, YEAR]

## static_date  (Attributes & Variables; StaticDateBlock, blocks/attributes-variables/static-date.block.ts)
- output: Static date
- fields: value

## static_date_time  (Attributes & Variables; StaticDateTimeBlock, blocks/attributes-variables/static-date-time.block.ts)
- output: Static date time
- fields: value

## currency_converter  (Attributes & Variables; CurrencyConvertorRateBlock, blocks/attributes-variables/currency-convertor-rate.block.ts)
- output: Currency Converter
- fields: conversion_type, LOYALTY_ACCOUNT
- live dropdowns: conversion_type [redemption_conversion, conversion, accrual_conversion, sponsor_award_rate, sponsor_base_rate]; LOYALTY_ACCOUNT (tenant catalog)

## activity_attribute  (Attributes & Variables; BitAttributesBlock, blocks/attributes-variables/bit-attributes.block.ts)
- output: Activity Attribute
- value inputs: RHS_INPUT [any], RHS_VALUE [any]
- fields: ATTRIBUTE, payment_operators, date_operator, datetime_operator, PAYMENT_MODE, payment_visible_value, payment_value, value
- live dropdowns: ATTRIBUTE (tenant catalog)
  - depending on the ATTRIBUTE chosen, adds: datetime_operator [CALENDAR_DATE, DATE, DATETIME, DAY_OF_MONTH, DAY_OF_WEEK, DAY_OF_YEAR, HOUR_OF_DAY, MINUTE_OF_HOUR, MONTH, WEEK_OF_YEAR, YEAR]; payment_operators [in, not_in]
- seen inside: arithmetic_generic.RHS (10), action.RHS_INPUT_VALUE (10), arithmetic_generic.LHS (5), if_generic_adaptive.RHS_INPUT (1), if_generic_adaptive.vector_counter_namespace (1), action.vector_counter_namespace (1)
- saved fields: datetime_operator=DATE/MONTH

## member_attribute  (Attributes & Variables; MemberAttributesBlock, blocks/attributes-variables/member-attributes.block.ts)
- output: Number
- value inputs: vector_counter_namespace [any], RHS_INPUT [any], RHS_VALUE [any]
- fields: ATTRIBUTE_TYPE, ATTRIBUTE, tieractionoperator, tierloyaltyaction, segment_id, date_operator, datetime_operator, selected_subscription_attribute, selected_partner_program_attribute, vector_counter_name, vector_counter_namespace, attribute_entity_map_level, index_datatype, onlycard_second_option, cardattribute, card_annual_option, operators
- live dropdowns: ATTRIBUTE_TYPE [MA_A, MA_S, MA_CA, MA_CC, MP_S, MA_O, MA_P, MA_SP, MA_T]
  - ATTRIBUTE_TYPE=MA_CA adds: ATTRIBUTE (tenant catalog)
  - ATTRIBUTE_TYPE=MA_CC adds: ATTRIBUTE (tenant catalog)
  - ATTRIBUTE_TYPE=MA_O adds: ATTRIBUTE (tenant catalog)
  - ATTRIBUTE_TYPE=MA_P adds: ATTRIBUTE (tenant catalog)
  - ATTRIBUTE_TYPE=MA_T adds: ATTRIBUTE (tenant catalog)
- seen inside: arithmetic_generic.RHS (7), if_generic_adaptive.RHS_INPUT (4), action.FORMULA (1), action.RHS_INPUT_VALUE (1), arithmetic_generic.LHS (1)
- saved fields: ATTRIBUTE_TYPE=MA_A/MA_O/MA_P; date_operator=DAY_OF_YEAR/MONTH

## product_attribute  (Attributes & Variables; ProductAttributesBlock, blocks/attributes-variables/product-attributes.block.ts)
- output: Product Attribute
- fields: ATTRIBUTE_PRODUCTS, ATTRIBUTE, date_operator, datetime_operator
- live dropdowns: ATTRIBUTE_PRODUCTS [products_attrs, product_cluster, product_offer_attributes]
  - ATTRIBUTE_PRODUCTS=products_attrs adds: ATTRIBUTE (tenant catalog)
  - ATTRIBUTE_PRODUCTS=product_cluster adds: ATTRIBUTE (tenant catalog)

## payment_attribute  (Attributes & Variables; PaymentAttributeBlock, blocks/attributes-variables/payment-attributes.block.ts)
- output: Payment Attribute
- value inputs: RHS_INPUT [any], RHS_VALUE [any]
- fields: payment_attribute, date_operator, operators, datetime_operator, value, visible_value
- live dropdowns: payment_attribute (tenant catalog)

## airport_attribute  (Attributes & Variables; AirportPairAttributeBlock, blocks/attributes-variables/airport-pair-attributes.block.ts)
- output: Payment Attribute
- fields: airportpair_attribute, date_operator, datetime_operator
- live dropdowns: airportpair_attribute [award_price, distance_flown, reward_price]
- seen inside: action.RHS_INPUT_VALUE (1)
- saved fields: airportpair_attribute=distance_flown

