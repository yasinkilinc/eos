package com.example.orders;

import org.springframework.stereotype.Component;

/**
 * Package-private on purpose, and this Javadoc contains a closing brace } and
 * the word class, neither of which ends anything.
 */
@Component("validateAgeCommand")
class ValidateAgeCommand implements OrderCommand {

    private final AgeLimitPolicy policy;

    ValidateAgeCommand(AgeLimitPolicy policy) {
        this.policy = policy;
    }

    @Override
    public CommandResult execute(OrderContext context) {
        if (!policy.permits(context.age())) {
            throw ValidationException.of("AGE_LIMIT", "too young");
        }
        if (context.age() > 120) {
            // No test names this one -- which is the whole point of `eos rules`.
            throw new ValidationException("AGE_IMPLAUSIBLE", "check the input");
        }
        return new CommandResult(true, null);
    }

    static final class Nested {
        void helper() { }
    }
}
