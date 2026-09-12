package com.example.api;

/**
 * An architecture test that names Spring annotations in rule strings.
 * It is a test, not a service, whatever those strings say.
 */
public class GreetArchTest {
    private static final String RULE = "classes annotated with @Service must live in ..service..";

    public String rule() {
        return RULE;
    }
}
