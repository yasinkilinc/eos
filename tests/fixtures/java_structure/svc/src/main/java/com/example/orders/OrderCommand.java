package com.example.orders;

/** A step in the configured order chain. */
public interface OrderCommand {
    // No modifier, and it ends in ';' not '{' -- invisible to a pattern that
    // required both.
    CommandResult execute(OrderContext context);
}
