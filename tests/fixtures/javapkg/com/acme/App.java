package com.acme;

import com.acme.Greeter;

public class App extends Base {
    private final Greeter greeter = new Greeter();

    public void run(String who) {
        init();
        String msg = greeter.greet(who);
        System.out.println(msg);
    }
}
