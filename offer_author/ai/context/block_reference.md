# Rules canvas blocks: the toolbox of the Offer Rules screen

Connections: a block's `previous` check must share a name with the check of the statement input (or `next`) it
plugs into; `output` blocks plug into value inputs. `any` = no check. You set fields (and `picked` codes for a
lookup popup); the tools fill the rest as the screen does.

## offer_generic  (Offer Level Block)
- statement inputs: activities [Activity], events [Event]

## activity  (Offer Level Block)
- previous: Activity
- next: Activity
- statement inputs: ACTIVITY_CRITERIA [Filter|OR], RULES [Rule]
- seen inside: offer_generic.activities (27)

## activity_criteria  (Offer Level Block)
- previous: Filter|OR
- next: Filter|OR
- value inputs: RHS_INPUT [any], RHS_VALUE [any], vector_counter_namespace [any]
- fields: OBJECT, attribute, attribute_level, airporttypes, selected_partner_program, fb_pages, operators, value, target_type_value, mto_ladder_value, vector_counter_name, vector_counter_namespace, attribute_entity_map_level, index_datatype, dummy_date_value, date_operator, datetime_operator, tiers, selected_partner_program_attribute, payment_operators, visible_value, blueprinttypes, selected_subscription_plan, selected_subscription_attribute, card_main_option, activecard_mainoperators, onlycard_second_option, cardvalue, card_annual_option, cardannualr_opperater, tierdefault, loyaltyaccount, opperater, opperatervalue, PAYMENT_MODE, payment_visible_value, payment_value
- operator between: its first value in RHS_INPUT, its second in RHS_VALUE (shown after "and")
- a LOOKUP / LOV attribute's codes: operators in, not_in take them in `picked` (the value popup); any other operator (==, !=) takes one code in the `value` field (a dropdown)
- live dropdowns: OBJECT [airport, airport_pair, activity="BIT", location, member, sponsor]
  - OBJECT=airport adds: airporttypes [destination="BIT Destination Airport", origin="BIT Origin Airport"]; attribute (tenant catalog)
  - OBJECT=airport_pair|activity|location|sponsor adds: attribute (tenant catalog)
  - OBJECT=member adds: attribute_level [MA_A="Activity-Level", MA_CA="Bank Account Level", MA_CC="Cobranded Card Level", MP_S="Individualization Attributes", MA_O="Offer-Level", MA_P="Program-Level", MA_SP="Subscription Level"]
- seen inside: activity.ACTIVITY_CRITERIA (29)
- saved picked codes: activity / activity_type / bit_type
- saved fields: OBJECT=activity/airport; operators===/between/in; airporttypes=origin; date_operator=DATE

## event  (Offer Level Block)
- previous: Event
- next: Event
- statement inputs: RULES [Rule]
- seen inside: offer_generic.events (3)

## or  (Offer Level Block)
- previous: OR|If
- next: OR|If
- statement inputs: OPERAND1 [If|Filter|OR], OPERAND2 [If|Filter|OR]

## rule  (Rule Level Block)
- previous: Billing Rule|Billing Action|Billing Rate / Rule|Action
- next: Billing Rule|Billing Action|Billing Rate / Rule|Action
- statement inputs: CONDITION [If|OR], ACTION [Billing Action|Billing Rate|Action|Rule]
- fields: IDENTIFIER
- seen inside: activity.RULES (63), event.RULES (9), rule.ACTION (7)

## rule_line  (Rule Level Block)
- previous: Rule|Action
- next: Rule|Action
- statement inputs: CONDITION [If|OR], ACTION [Action]
- fields: IDENTIFIER
- seen inside: activity.RULES (22)

## payment_rule  (Rule Level Block)
- previous: Rule
- next: Rule
- statement inputs: CONDITION [If Payment], ACTION [Action]
- fields: IDENTIFIER

## if_generic_adaptive  (Rule Level Block)
- previous: If
- next: If
- value inputs: RHS_INPUT [any], RHS_VALUE [any], vector_counter_namespace [any]
- fields: OBJECT, value, attribute, attribute_level, third_parties_dropdown, ProductOrOfferContext, airporttypes, name, visible_value, blueprinttypes, selected_subscription_plan, selected_partner_program, selected_subscription_attribute, fb_pages, operators, target_type_value, mto_ladder_value, vector_counter_name, vector_counter_namespace, attribute_entity_map_level, index_datatype, dummy_date_value, date_operator, datetime_operator, tiers, selected_partner_program_attribute, payment_operators, card_main_option, activecard_mainoperators, onlycard_second_option, cardvalue, card_annual_option, cardannualr_opperater, PAYMENT_MODE, payment_visible_value, payment_value, tierdefault, loyaltyaccount, opperater, opperatervalue
- operator between: its first value in RHS_INPUT, its second in RHS_VALUE (shown after "and")
- a LOOKUP / LOV attribute's codes: operators in, not_in take them in `picked` (the value popup); any other operator (==, !=) takes one code in the `value` field (a dropdown)
- live dropdowns: OBJECT [third_party="3rd Party", airport, airport_pair, activity="BIT", location, member, products_attrs="Product", sponsor]
  - OBJECT=third_party adds: third_parties_dropdown [ACTIVITY_REFERRER, COMPANION, CORPORATE, EMPLOYEE, ENROLLMENT_REFERRER, FAMILY_HEAD, INSTITUTION, TRANSFEREE]; attribute_level [MA_A="Activity-Level", MA_S="Analytics Write-back", MA_CA="Bank Account Level", MA_CC="Cobranded Card Level", MA_O="Offer-Level", MA_P="Program-Level", MA_SP="Subscription Level"]
  - OBJECT=airport adds: airporttypes [destination="BIT Destination Airport", origin="BIT Origin Airport"]; attribute (tenant catalog)
  - OBJECT=airport_pair|activity|location|sponsor adds: attribute (tenant catalog)
  - OBJECT=member adds: attribute_level [MA_A="Activity-Level", MA_S="Analytics Write-back", MA_CA="Bank Account Level", MA_CC="Cobranded Card Level", MP_S="Individualization Attributes", MA_O="Offer-Level", MA_P="Program-Level", MA_SP="Subscription Level"]
  - OBJECT=products_attrs adds: ProductOrOfferContext [products_attrs="Attributes", product_cluster="Cluster", product_offer_attributes="Offer Context"]; attribute (tenant catalog)
- seen inside: rule.CONDITION (105), rule_line.CONDITION (29)
- saved picked codes: eligible_product_category_ / eligible_product_category_2 / eligible_product_category_3 / menu_1 / menu_2 / menu_3
- saved fields: OBJECT=activity/member/products_attrs/sponsor; datetime_operator=DAY_OF_WEEK/DAY_OF_YEAR/HOUR_OF_DAY/MONTH; operators=!=/</<=/==/>/in; attribute_level=MA_A/MA_O; ProductOrOfferContext=products_attrs; date_operator=MONTH; vector_counter_name=unique/Activity Type/Destination Airport/H_Sponsor ID/Line Product/Sponsor; vector_counter_namespace=h_bit_type/h_destination_airport/h_location/h_sponsor_id/l_product_external_id

## if_payment_attribute  (Rule Level Block)
- previous: If Payment
- next: If Payment
- value inputs: RHS_INPUT [any], RHS_VALUE [any]
- fields: payment_attribute, date_operator, operators, datetime_operator, value, visible_value, PAYMENT_MODE
- operator between: its first value in RHS_INPUT, its second in RHS_VALUE (shown after "and")
- a LOOKUP / LOV attribute's codes: operators in, not_in take them in `picked` (the value popup); any other operator (==, !=) takes one code in the `value` field (a dropdown)
- live dropdowns: payment_attribute (tenant catalog)

## action  (Rule Level Block)
- previous: Action
- next: Action
- value inputs: FORMULA [any], LOYALTY_ACCOUNT [any], PRIVILEGE_types [any], EXTEND_PRIVILEGE [any], TransferPoints [any], SWEEPSTAKES_LIST [any], PLAY_WIN_TYPE [any], RHS_INPUT [any], REWARD_TAGS [any], RHS_INPUT_CUSTOMTIEREND [Number|Arithmetic date|Static date|Static date time|Activity Attribute|Tiers|Member|current_date_block|Current Date|Blueprint Variable], vector_counter_namespace [any], RHS_INPUT_VALUE [any], DYNAMIC_RHS_INPUT [Number], PRIVILEGE [any], PRIVILEGE_USAGEPOLICY [any], PRIVILEGE_QUANTITY [any], VALIDITY_START_DATE [any], VALIDITY_END_DATE [any]
- fields: action_type, LOYALTY_ACCOUNT, SELECTED_PRIVILEGE, mta_type_value, WHAT, SELECTED_EXPIRE_LOYALTY_ACC, sweepstakeid, playwintype, playwinsetup, playwinusagepolicy, selected_card_id, SELECTED_EXP_OPERATOR, tiername, tierenddateopt, REWARD_TAGS, PENDING_DAYS_VALUE, mta_attribute, value, action_operator, vector_counter_name, vector_counter_namespace, attribute_entity_map_level, index_datatype, target_type_value, mto_ladder_value, PRIVILEGE_USAGEPOLICY, Privilege_Quantity, previlege_Validity, dynamic_privilege_config, visible_value, blueprinttypes
- live dropdowns: action_type [assign_play_win_reward, give_privilege="Assign Privilege", assign_sweepstakes, expire_points, take_points="Redeem Points", reset_points, return_points, give_points="Reward Points", update_attribute]
  - action_type=assign_play_win_reward adds: playwintype [SCRATCH_CARD, SPIN_THE_WHEEL]; playwinsetup []
  - action_type=give_privilege adds: SELECTED_PRIVILEGE (tenant catalog); PRIVILEGE_USAGEPOLICY (tenant catalog); <value:PRIVILEGE_QUANTITY> [Number, Static Value, Activity Attribute, Product Attribute, Payment Attribute, Arithmetic, Blueprint Variable]; previlege_Validity [product_validity="Default", custom_validity="Custom"]
  - action_type=assign_sweepstakes adds: sweepstakeid []
  - action_type=expire_points adds: SELECTED_EXPIRE_LOYALTY_ACC (tenant catalog)
  - action_type=take_points|return_points adds: LOYALTY_ACCOUNT (tenant catalog); <value:FORMULA> [Number, Static Value, Activity Attribute, Product Attribute, Currency Converter, Arithmetic, Payment Attribute, Blueprint Variable]
  - action_type=reset_points adds: LOYALTY_ACCOUNT (tenant catalog)
  - action_type=give_points adds: LOYALTY_ACCOUNT (tenant catalog); REWARD_TAGS (tenant catalog); <value:FORMULA> [Number, Static Value, Activity Attribute, Product Attribute, Currency Converter, Arithmetic, Payment Attribute, Blueprint Variable]
  - action_type=update_attribute adds: mta_type_value [MA_A="Activity-Level", MA_O="Offer-Level"] and more (tenant catalog); mta_attribute [new]
- seen inside: rule.ACTION (104), rule_line.ACTION (31)
- saved fields: action_type=give_points/give_privilege/update_attribute; action_operator=INCREMENT_BY/RESET/SET; previlege_Validity=product_validity; vector_counter_name=Sponsor/Activity Type/Destination Airport/H_Sponsor ID/Line Product/Location; vector_counter_namespace=h_bit_type/h_destination_airport/h_location/h_sponsor_id/l_product_external_id

## action_third_party  (Rule Level Block)
- previous: Action
- next: Action
- value inputs: LOYALTY_ACCOUNT [any], PRIVILEGE_types [any], FORMULA [any], TransferPoints [any], PLAY_WIN_TYPE [any], SWEEPSTAKES_LIST [any], RHS_INPUT [Number|Arithmetic date|Static date|Activity Attribute|Tiers|Member|Current Date|Blueprint Variable], RHS_INPUT_CUSTOMTIEREND [Number|Arithmetic date|Static date|Activity Attribute|Tiers|Member|Current Date|Blueprint Variable], REWARD_TAGS [any], vector_counter_namespace [any], RHS_INPUT_VALUE [any], PRIVILEGE_USAGEPOLICY [any], PRIVILEGE [any], PRIVILEGE_QUANTITY [any], VALIDITY_START_DATE [any], VALIDITY_END_DATE [any]
- fields: third_parties_dropdown, action_type, LOYALTY_ACCOUNT, tierclassid, SELECTED_PRIVILEGE, mta_type_value, WHAT, Transfer_P_L, playwintype, sweepstakesetup, playwinsetup, playwinusagepolicy, selected_card_id, tierlevelid, tierenddateopt, REWARD_TAGS, PENDING_DAYS_VALUE, mta_attribute, value, action_operator, vector_counter_name, vector_counter_namespace, attribute_entity_map_level, index_datatype, target_type_value, mto_ladder_value, PRIVILEGE_USAGEPOLICY, Privilege_Quantity, previlege_Validity
- live dropdowns: third_parties_dropdown [ACTIVITY_REFERRER, COMPANION, CORPORATE, EMPLOYEE, ENROLLMENT_REFERRER, FAMILY_HEAD, INSTITUTION, TRANSFEREE]
  - third_parties_dropdown=ACTIVITY_REFERRER|COMPANION|CORPORATE|EMPLOYEE|ENROLLMENT_REFERRER|FAMILY_HEAD|INSTITUTION adds: action_type [assign_play_win_reward, give_privilege="Assign Privilege", assign_sweepstake, give_points="Reward Points", transfer_points, update_attribute]
  - third_parties_dropdown=TRANSFEREE adds: action_type [transfer_points, transfer_privilege]

## static_value  (Attributes & Variables)
- output: Static Value
- fields: value
- value: the screen accepts only a value matching `^[0-9]*\.?[0-9]+$`; any other value is a screen error ("<value> is not valid number")
- seen inside: action.RHS_INPUT_VALUE (38), if_generic_adaptive.RHS_INPUT (15), arithmetic_generic.RHS (3)
- saved fields: value=0/1/100/2/3/4

## static_string_value  (Attributes & Variables)
- output: Static String Value
- fields: value
- seen inside: action.RHS_INPUT (27)
- saved fields: value=Yes

## arithmetic_generic  (Attributes & Variables)
- output: Arithmetic
- value inputs: LHS [Static Value|Activity Attribute|Product Attribute|Payment Attribute|Number|Currency Converter|Arithmetic|Blueprint Variable|Current Date], RHS [Static Value|Activity Attribute|Product Attribute|Payment Attribute|Number|Currency Converter|Arithmetic|Blueprint Variable|Current Date]
- fields: operator
- live dropdowns: operator [/, *="X", +, -, %]
- seen inside: action.FORMULA (14), action.RHS_INPUT_VALUE (5), if_generic_adaptive.RHS_INPUT (3), arithmetic_generic.LHS (2)
- saved fields: operator=*/+/-//

## arithmetic_date  (Attributes & Variables)
- output: Arithmetic date
- value inputs: LHS [Activity Attribute|Current Date|Product Attribute|Payment Attribute|Number|Static date|Static date time|Static String Value|Arithmetic date|Blueprint Variable]
- fields: operator, number, date_quantity, snap_to_quntity
- live dropdowns: operator [+, -, snapTO]; date_quantity [DAYS, MONTHS, YEARS]
  - operator=snapTO adds: snap_to_quntity [MONTH, YEAR]

## current_date_block  (Attributes & Variables)
- output: Current Date
- fields: current_date_type
- live dropdowns: current_date_type [DATE, DATETIME, DAY_OF_MONTH, DAY_OF_WEEK, DAY_OF_YEAR, HOUR_OF_DAY, MINUTE_OF_HOUR, MONTH, WEEK_OF_YEAR, YEAR]

## static_date  (Attributes & Variables)
- output: Static date
- fields: value
- value: written in the format `YYYY-MM-DD` (dayjs tokens), as the screen's picker writes it

## static_date_time  (Attributes & Variables)
- output: Static date time
- fields: value
- value: written in the format `YYYY-MM-DD h:mm A` (dayjs tokens), as the screen's picker writes it

## currency_converter  (Attributes & Variables)
- output: Currency Converter
- fields: conversion_type, LOYALTY_ACCOUNT
- live dropdowns: conversion_type [redemption_conversion="Award", conversion="Exchange", accrual_conversion="Reward", sponsor_award_rate="Sponsor Award", sponsor_base_rate="Sponsor Reward"]; LOYALTY_ACCOUNT (tenant catalog)

## activity_attribute  (Attributes & Variables)
- output: Activity Attribute
- value inputs: RHS_INPUT [any], RHS_VALUE [any]
- fields: ATTRIBUTE, payment_operators, date_operator, datetime_operator, PAYMENT_MODE, payment_visible_value, payment_value, value
- operator between: its first value in RHS_INPUT, its second in RHS_VALUE (shown after "and")
- a LOOKUP / LOV attribute's codes: operators in, not_in take them in `picked` (the value popup); any other operator (==, !=) takes one code in the `value` field (a dropdown)
- live dropdowns: ATTRIBUTE (tenant catalog)
  - depending on the ATTRIBUTE chosen, adds: datetime_operator [CALENDAR_DATE, DATE, DATETIME, DAY_OF_MONTH, DAY_OF_WEEK, DAY_OF_YEAR, HOUR_OF_DAY, MINUTE_OF_HOUR, MONTH, WEEK_OF_YEAR, YEAR]; payment_operators [in, not_in]
- seen inside: arithmetic_generic.RHS (10), action.RHS_INPUT_VALUE (10), arithmetic_generic.LHS (5), if_generic_adaptive.RHS_INPUT (1), if_generic_adaptive.vector_counter_namespace (1), action.vector_counter_namespace (1)
- saved fields: datetime_operator=DATE/MONTH

## member_attribute  (Attributes & Variables)
- output: Number
- value inputs: vector_counter_namespace [any], RHS_INPUT [any], RHS_VALUE [any]
- fields: ATTRIBUTE_TYPE, ATTRIBUTE, tieractionoperator, tierloyaltyaction, segment_id, date_operator, datetime_operator, selected_subscription_attribute, selected_partner_program_attribute, vector_counter_name, vector_counter_namespace, attribute_entity_map_level, index_datatype, onlycard_second_option, cardattribute, card_annual_option, operators
- operator between: its first value in RHS_INPUT, its second in RHS_VALUE (shown after "and")
- live dropdowns: ATTRIBUTE_TYPE [MA_A="Activity-Level", MA_S="Analytics Write-back", MA_CA="Bank Account Level", MA_CC="Cobranded Card Level", MP_S="Individualization Attributes", MA_O="Offer-Level", MA_P="Program-Level", MA_SP="Subscription Level", MA_T="Tier Level"]
  - ATTRIBUTE_TYPE=MA_CA|MA_CC|MA_O|MA_P|MA_T adds: ATTRIBUTE (tenant catalog)
- seen inside: arithmetic_generic.RHS (7), if_generic_adaptive.RHS_INPUT (4), action.FORMULA (1), action.RHS_INPUT_VALUE (1), arithmetic_generic.LHS (1)
- saved fields: ATTRIBUTE_TYPE=MA_A/MA_O/MA_P; date_operator=DAY_OF_YEAR/MONTH

## product_attribute  (Attributes & Variables)
- output: Product Attribute
- fields: ATTRIBUTE_PRODUCTS, ATTRIBUTE, date_operator, datetime_operator
- live dropdowns: ATTRIBUTE_PRODUCTS [products_attrs="Product", product_cluster, product_offer_attributes="Product Offer Context"]
  - ATTRIBUTE_PRODUCTS=products_attrs|product_cluster adds: ATTRIBUTE (tenant catalog)

## payment_attribute  (Attributes & Variables)
- output: Payment Attribute
- value inputs: RHS_INPUT [any], RHS_VALUE [any]
- fields: payment_attribute, date_operator, operators, datetime_operator, value, visible_value
- operator between: its first value in RHS_INPUT, its second in RHS_VALUE (shown after "and")
- a LOOKUP / LOV attribute's codes: operators in, not_in take them in `picked` (the value popup); any other operator (==, !=) takes one code in the `value` field (a dropdown)
- live dropdowns: payment_attribute (tenant catalog)

## airport_attribute  (Attributes & Variables)
- output: Payment Attribute
- fields: airportpair_attribute, date_operator, datetime_operator
- live dropdowns: airportpair_attribute [award_price, distance_flown, reward_price="Reward Value"] and more (tenant catalog)
- seen inside: action.RHS_INPUT_VALUE (1)

